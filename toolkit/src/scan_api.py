"""
scan_api.py (extended)

Same as before, plus repo-level aggregation of the new per-file fields from
pattern_detector.py: named_stores, store_agent_links, write_sites, read_sites.
Produces shared_across_agents, rag_writers, rag_readers, unsanitized_writes --
the RADAR paper's Structure/RAG metadata fields -- as repo-summary output.

Each repo is analysed in two passes: the first finds every store and agent,
the second re-analyses the files that import one of them from another file
(see _collect_exports), so a write, read, call or `Agent(memory=kb)` link
made in a different file from the creation is attributed to that specific
store/agent.

KNOWN LIMITATIONS (disclosed, not hidden):
  - Cross-file resolution is ONE hop through a direct named import
    (`from util import client`, `import { kb } from "./store"`). A store
    that reaches its user through a function parameter, a return value, a
    re-export (`export * from`), a default export or a tsconfig path alias
    is still not linked, and shared_across_agents / rag_writers /
    rag_readers under-count in exactly those situations.
  - Aliased imports (`from chromadb import Client as ChromaClient`) are not
    resolved back to their canonical constructor name, so both agent and
    store detection can miss aliased constructor calls. Same root cause as
    the framework-attribution tie noted in CompiledCategory's docstring --
    both need import-statement-based resolution to fully close.
  - Two frameworks sharing an IDENTICAL bare pattern (e.g. Agno and CrewAI
    both listing "Agent(") cannot be disambiguated by pattern specificity
    alone; this is a tie that import-based resolution would also fix.
"""

import argparse
import gc
import io
import os
import re
import sys
import tarfile
import time
from pathlib import Path

from .github_fetcher import GitHubClient, GITHUB_API, _parse_owner_repo
from .incremental_json import IncrementalJSONArrayWriter, already_processed_keys
from .pattern_detector import PatternDetector, EXTENSION_LANGUAGE_MAP, apply_framework_confirmation
from .util import RunMetadata, timestamped_results_path, utc_now_iso

_PACKAGE_DIR = Path(__file__).parent
_DEFAULT_PATTERNS_PATH = _PACKAGE_DIR / "data" / "patterns_verified.json"
_DEFAULT_RESULTS_DIR = _PACKAGE_DIR.parent / "output" / "scan_results"

EXCLUDED_DIR_NAMES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".turbo", "site-packages", ".mypy_cache",
    ".pytest_cache", "coverage", ".tox", "vendor", "test", "tests", "evals",
    "examples", "example", "demo", "demos", "tutorial", "tutorials",
    "showcase", "cookbook",
}
RELEVANT_EXTENSIONS = set(EXTENSION_LANGUAGE_MAP.keys())

# Test files colocated next to source (not inside test/tests dirs) -- confirmed a major over-count source, e.g. getpaseo/paseo's agent-manager.test.ts alone contributed 180 of its 609 detected "agents".
EXCLUDED_FILENAME_RE = re.compile(
    r"\.(?:test|spec)\.[jt]sx?$|(?:^|/)test_[^/]+\.py$|_test\.py$", re.IGNORECASE
)


def fetch_tarball_bytes(client, owner, repo, ref="HEAD"):
    resp = client._get(f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{ref}")
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "no response"
        return None, f"tarball fetch failed ({code})"
    return resp.content, None


def iter_tarball_source_files(tar_bytes):
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        members = tar.getmembers()
        if not members:
            return
        top = members[0].name.split("/")[0]
        for member in members:
            if not member.isfile():
                continue
            name = member.name
            if not name.startswith(top + "/"):
                continue
            rel = name[len(top) + 1:]
            if not rel:
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in rel.split("/")):
                continue
            if EXCLUDED_FILENAME_RE.search(rel):
                continue

            suffix = Path(rel).suffix.lower()
            is_relevant = suffix in RELEVANT_EXTENSIONS
            if not is_relevant:
                yield rel, None, member.size, False
                continue

            f = tar.extractfile(member)
            if f is None:
                yield rel, None, member.size, True
                continue
            try:
                raw = f.read()
            except Exception:
                yield rel, None, member.size, True
                continue
            text = raw.decode("utf-8", errors="replace")
            yield rel, text, member.size, True


def _python_module_keys(rel):
    """Every dotted path a Python file can be imported as: `src/app/store.py`
    -> {"store", "app.store", "src.app.store"}. All suffixes, because the
    import root (src layout, a package subdirectory, a relative import whose
    dots ast drops) isn't knowable from the file alone."""
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return {".".join(parts[i:]) for i in range(len(parts))}


def _collect_exports(results):
    """
    First-pass output -> the external_exports table analyze_source takes:
    {"stores": {module: {var: framework}}, "agents": {...},
     "store_ids": {module: {var: "file:line"}}}.

    `module` is each dotted path the file can be imported as (Python) or its
    repo-relative path (JS/TS). Built from store_vars/agent_vars, so a
    handle (`kb = client.get_collection(..)`) is importable like the store
    itself. Only a plain variable can be imported by name, so `self.kb` /
    `this.kb` are not exported. When two files could both be the `store` in
    `from store import kb`, that name is dropped rather than guessed.
    """
    exports = {"stores": {}, "agents": {}, "store_ids": {}}
    owner, ambiguous = {}, set()

    def put(kind, module, var, value, rel):
        if owner.setdefault((kind, module, var), rel) != rel:
            ambiguous.add((module, var))
        exports[kind].setdefault(module, {}).setdefault(var, value)

    for rel, result in results.items():
        modules = _python_module_keys(rel) if result.language == "python" else {rel}
        for module in modules:
            for var, (framework, line) in result.store_vars.items():
                if "." not in var and line is not None:
                    put("stores", module, var, framework, rel)
                    put("store_ids", module, var, f"{rel}:{line}", rel)
            for var, framework in result.agent_vars.items():
                if "." not in var:
                    put("agents", module, var, framework, rel)
    for module, var in ambiguous:
        for table in exports.values():
            table.get(module, {}).pop(var, None)
    return {kind: {m: v for m, v in table.items() if v} for kind, table in exports.items()}


def _export_import_hint(exports):
    """Cheap text prefilter for the second pass: a regex matching an import
    line that could name an exporting module, so only those files are
    analysed twice. None when nothing is exported."""
    python_names, js_names = set(), set()
    for table in (exports["stores"], exports["agents"]):
        for module in table:
            if "/" in module or Path(module).suffix:
                stem = Path(module).stem
                js_names.add(Path(module).parent.name if stem == "index" else stem)
            else:
                python_names.add(module.rpartition(".")[2])
    parts = []
    if python_names:
        names = "|".join(sorted(map(re.escape, python_names)))
        parts.append(rf"^[ \t]*(?:from|import)[ \t][^\n]*\b(?:{names})\b")
    if js_names:
        names = "|".join(sorted(map(re.escape, js_names - {""})))
        parts.append(rf"""['"]\.{{1,2}}/[^'"\n]*(?:{names})""")
    return re.compile("|".join(parts), re.MULTILINE) if parts else None


def _is_local_python_import(canonical, local_modules):
    """`from agents import researcher` in a repo with its own agents/
    package is not the OpenAI Agents SDK. `canonical` is module.Symbol or a
    bare module."""
    return canonical in local_modules or canonical.rpartition(".")[0] in local_modules


def scan_tarball(detector, tar_bytes):
    """
    Mirrors scan_local.py's scan_one_repo(), sourced from tarball bytes.

    Simplified output, by design: everything the scan finds is collected
    into ONE flat `findings` list (name + matched pattern text + line +
    framework, all together, sortable by file/line for manual
    spot-checking) instead of eight overlapping raw-count structures.
    radar_summary is still computed from the fuller per-file structured
    data internally (store_agent_links, write/read attribution, etc.) --
    that machinery didn't go away, it's just not all surfaced in the
    output anymore. If you need the old raw category/pattern-count detail
    back, it's still available via detector.analyze_source(...).categories
    per file; it's just not aggregated into the repo-level result by default.
    """
    total_files = 0
    files_with_parse_errors = 0
    findings = []
    agent_instances = []   # kept internally for radar_summary math (not returned raw)
    custom_agent_instances = []
    stores = {}  # "file:line" -> one entry per store instance
    store_agent_links = []
    write_sites = []
    read_sites = []
    results = {}  # rel -> FileResult, in tarball order

    # Supply local JS/TS modules for tools passed through imported factories.
    javascript_sources = {
        rel: text for rel, text, size, relevant in iter_tarball_source_files(tar_bytes)
        if relevant and text is not None and size <= detector.max_file_size_bytes
        and EXTENSION_LANGUAGE_MAP.get(Path(rel).suffix.lower()) == "javascript"
    }
    seen_skill_files = set()
    for rel, text, size_bytes, is_relevant in iter_tarball_source_files(tar_bytes):
        total_files += 1
        if (Path(rel).name == "SKILL.md" and rel not in seen_skill_files
                and size_bytes <= detector.max_file_size_bytes):
            seen_skill_files.add(rel)
            findings.append({
                "type": "skill", "name": Path(rel).parent.name or None,
                "framework": None, "matched": "SKILL.md", "source": "file",
                "file": rel, "line": 1,
            })
        if not is_relevant or text is None:
            continue

        result = detector.analyze_source(text, rel, size_bytes=size_bytes,
                                         javascript_sources=javascript_sources)
        if result.skipped_reason:
            continue
        results[rel] = result

    # Second pass: files importing a store/agent another file created.
    exports = _collect_exports(results)
    hint = _export_import_hint(exports)
    if hint is not None:
        for rel, text, size_bytes, is_relevant in iter_tarball_source_files(tar_bytes):
            if rel in results and text is not None and hint.search(text):
                results[rel] = detector.analyze_source(
                    text, rel, size_bytes=size_bytes,
                    javascript_sources=javascript_sources, external_exports=exports)

    def store_id(rel, ref):
        # a creation line in this file, or already a "file:line" id for an imported store
        return ref if isinstance(ref, str) or ref is None else f"{rel}:{ref}"

    local_python_modules = {
        module for rel, result in results.items() if result.language == "python"
        for module in _python_module_keys(rel)
    }
    imported_frameworks = set()
    framework_call_counts = {}

    files_scanned = len(results)
    for rel, result in results.items():
        # `files_scanned` already counted this file above -- a parse error
        # (e.g. unresolved git-conflict markers, see test_counts.py) does
        # NOT skip a file, it just leaves it with empty findings, so
        # "scanned" alone doesn't tell you whether a file's contents were
        # actually analyzed. Surfaced separately rather than folded into
        # skipped_reason, so files_scanned keeps its existing meaning for
        # any code/report already relying on it.
        if result.parse_error:
            files_with_parse_errors += 1
        is_python = result.language == "python"

        for f in result.findings:
            f = {**f, "file": rel}
            if "store" in f:
                f["store"] = store_id(rel, f["store"])
            findings.append(f)

        for a in result.named_agents:
            agent_instances.append({"name": a["name"], "tools_bound": a["tools_bound"]})
        for c in result.custom_agents:
            custom_agent_instances.append(c)
        for s in result.named_stores:
            stores.setdefault(f"{rel}:{s['line']}", {
                "id": f"{rel}:{s['line']}", "variable": s["variable"], "framework": s["framework"],
                "file": rel, "line": s["line"], "n_writes": 0, "n_reads": 0, "agents": set(),
            })
        for link in result.store_agent_links:
            store_agent_links.append({**link, "store_id": store_id(rel, link.get("store_ref"))})
        for w in result.write_sites:
            write_sites.append({**w, "store_id": store_id(rel, w.get("store"))})
        for r in result.read_sites:
            read_sites.append({**r, "store_id": store_id(rel, r.get("store"))})

        for imp in result.imports:
            if imp["framework"] and not (is_python and _is_local_python_import(imp["canonical"], local_python_modules)):
                imported_frameworks.add(imp["framework"])
        for framework, by_module in result.framework_uses.items():
            n = sum(count for module, count in by_module.items()
                    if not (is_python and _is_local_python_import(module, local_python_modules)))
            if n:
                framework_call_counts[framework] = framework_call_counts.get(framework, 0) + n

    findings.sort(key=lambda f: (f["file"], f["line"]))

    # Evidence for both agent counts below.
    agent_evidence = [
        {"name": f["name"], "framework": f["framework"], "matched": f["matched"],
         "file": f["file"], "line": f["line"], "line_content": f.get("line_content")}
        for f in findings if f["type"] == "agent"
    ]
    custom_agent_evidence = [
        {"name": f["name"], "framework": f["framework"], "matched": f["matched"],
         "file": f["file"], "line": f["line"], "line_content": f.get("line_content"),
         "tool_names": f.get("tool_names")}
        for f in findings if f["type"] == "custom_agent"
    ]
    tools_evidence = [
        {"name": tool_name, "agent": f["name"], "framework": f["framework"],
         "file": f["file"], "line": f["line"], "line_content": f.get("line_content"),
         "source": "agent_tools_kwarg"}
        for f in findings if f["type"] == "agent"
        for tool_name in (f.get("tools_bound") or [])
    ] + [
        {"name": tool_name, "agent": f["name"], "framework": f["framework"],
         "file": f["file"], "line": f["line"], "line_content": f.get("line_content"),
         "source": "model_tool_binding"}
        for f in findings if f["type"] == "custom_agent"
        for tool_name in (f.get("tool_names") or [])
    ] + [
        {"name": f["name"], "agent": None, "framework": f["framework"],
         "file": f["file"], "line": f["line"], "line_content": f.get("line_content"),
         "source": "confirmed_tool_definition"}
        for f in findings if f["type"] == "confirmed_tool_definition" and f.get("name")
    ]
    tool_definition_evidence = [
        {"file": f["file"], "line": f["line"], "matched": f["matched"],
         "line_content": f.get("line_content")}
        for f in findings if f["type"] == "tool_definition"
    ]

    # n_agents counts every confirmed agent-creation call site, not unique
    # names -- deduping by name repo-wide was collapsing distinct agents in
    # different files that happen to share a common local variable name.
    n_agents = len(agent_instances)
    tools_bound_all = set()
    for a in agent_instances:
        tools_bound_all.update(a.get("tools_bound", []))
    for c in custom_agent_instances:
        tools_bound_all.update(c.get("tool_names") or [])
    tools_bound_all.update(
        ev["name"] for ev in tools_evidence if ev["source"] == "confirmed_tool_definition"
    )
    n_tools = len(tools_bound_all)
    has_rag = len(stores) > 0

    # Everything below is keyed by store INSTANCE (its "file:line" id), not
    # by variable name: two unrelated `kb` variables in different files are
    # two stores, and one store imported into three files is still one.
    for link in store_agent_links:
        if link["store_id"] in stores and link["agent"]:
            stores[link["store_id"]]["agents"].add(link["agent"])
    for sites, counter in ((write_sites, "n_writes"), (read_sites, "n_reads")):
        for site in sites:
            if site["store_id"] in stores:
                stores[site["store_id"]][counter] += 1
    shared_across_agents = any(len(s["agents"]) >= 2 for s in stores.values())

    rag_writers = sorted({agent for s in stores.values() if s["n_writes"] for agent in s["agents"]})
    rag_readers = sorted({agent for s in stores.values() if s["n_reads"] for agent in s["agents"]})
    unsanitized_writes = any(w["unsanitized"] for w in write_sites)

    # A store that is written but never read is a log/sink rather than
    # retrieval memory; `role` keeps the two apart.
    store_evidence = [{
        **s, "agents": sorted(s["agents"]),
        "role": ("read_write" if s["n_writes"] and s["n_reads"] else
                 "write_only" if s["n_writes"] else "read_only" if s["n_reads"] else "unused"),
    } for s in stores.values()]

    # n_custom_agents: hand-rolled agents (no tracked framework) confirmed
    # only via real tool-calling evidence. NOT guaranteed disjoint from
    # n_agents: a file can have a framework-confirmed agent (e.g. a
    # LangGraph StateGraph) AND separately bind tools to a model via
    # `.bind_tools(` for one of that graph's nodes -- the same construction
    # step, not a second agent. n_agents + n_custom_agents is a ceiling on
    # a repo's agent count, not a verified total.
    custom_agent_llm_tool_names = sorted({
        name for c in custom_agent_instances for name in (c.get("tool_names") or [])
    })
    # One skill per eligible SKILL.md path, including mirrored copies.
    skill_evidence = [f for f in findings if f['type'] == 'skill']

    radar_summary = {
        "n_agents": n_agents,
        "agent_evidence": agent_evidence,
        "n_custom_agents": len(custom_agent_instances),
        "custom_agent_evidence": custom_agent_evidence,
        "has_confirmed_llm_tool_calling": len(custom_agent_instances) > 0,
        "llm_tool_names": custom_agent_llm_tool_names,
        "n_tools": n_tools,
        "tools_evidence": tools_evidence,
        "n_skills": len(skill_evidence),
        "skill_evidence": skill_evidence,
        "n_confirmed_tool_definitions": sum(
            1 for ev in tools_evidence if ev["source"] == "confirmed_tool_definition"
        ),
        "n_tool_definition_markers": len(tool_definition_evidence),
        "tool_definition_evidence": tool_definition_evidence,
        "has_rag": has_rag,
        "n_stores": len(stores),
        "store_evidence": store_evidence,
        "store_frameworks": sorted({s["framework"] for s in stores.values()}),
        "n_stores_written": sum(1 for s in stores.values() if s["n_writes"]),
        "n_stores_read": sum(1 for s in stores.values() if s["n_reads"]),
        "rag_write_frameworks": sorted({w["framework"] for w in write_sites}),
        "rag_read_frameworks": sorted({r["framework"] for r in read_sites}),
        "shared_across_agents": shared_across_agents,
        "rag_writers": rag_writers,
        "rag_readers": rag_readers,
        "unsanitized_writes": unsanitized_writes,
    }

    # framework_comparison now reads off `findings` (single-attribution,
    # already deduplicated) instead of raw pattern-match counts -- this
    # also fixes the earlier noise where a repo using only Chroma would
    # show five other frameworks under detected_only, just because they
    # shared a generic verb.
    detected_frameworks = {f["framework"] for f in findings if f.get("framework")}

    return {
        "total_files": total_files,
        "files_scanned": files_scanned,
        "files_with_parse_errors": files_with_parse_errors,
        "radar_summary": radar_summary,
        "findings": findings,
        "_detected_frameworks": sorted(detected_frameworks),  # consumed by build_output_record, not meant as final output
        "_imported_frameworks": sorted(imported_frameworks),  # same
        "_framework_call_counts": framework_call_counts,  # same
    }


def build_output_record(entry, scan_result, status, error=None):
    declared = {f["name"] for f in entry.get("frameworks", [])}
    detected = set(scan_result.pop("_detected_frameworks", [])) if scan_result else set()
    imported = set(scan_result.pop("_imported_frameworks", [])) if scan_result else set()
    call_counts = scan_result.pop("_framework_call_counts", {}) if scan_result else {}
    record = {
        "repo_name": entry.get("name"),
        "repo_url": entry.get("url"),
        "stars": entry.get("stars"),
        "declared_frameworks": sorted(declared),
        "status": status,
        "error": error,
        "scanned_at": utc_now_iso(),
    }
    if scan_result:
        # radar_summary goes right after the identifying fields above, so
        # it's the first thing visible when you open a repo's record --
        # everything else (raw per-file data, full site lists) is bulkier
        # and more useful for drilling in later, not for a first glance.
        record["radar_summary"] = scan_result.pop("radar_summary", None)
        record.update(scan_result)
        record["framework_comparison"] = {
            "agrees": sorted(declared & detected),
            "declared_only": sorted(declared - detected),
            "detected_only": sorted(detected - declared),
        }
        # Stricter version of the crawler's dependency filter: declaring or
        # importing a framework is not enough, application code (tests,
        # examples and vendored directories are never scanned) has to call
        # something imported from it. `instantiated` is exactly that: at
        # least one call, decorator or subclass whose name traces back to
        # an import of the framework's package.
        instantiated = set(call_counts)
        record["framework_usage"] = {
            "instantiated": sorted(instantiated),
            "imported_only": sorted(imported - instantiated),
            "declared_only": sorted(declared - imported - instantiated),
            "call_counts": dict(sorted(call_counts.items())),
            "passes_instantiation_filter": bool(instantiated),
        }
    return record


def run(repos_json_path, out_path, patterns_path, token, max_file_size_bytes, limit, gc_every):
    import json
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("No GitHub token found. Set GITHUB_TOKEN or pass --token.")

    corpus = json.loads(Path(repos_json_path).read_text(encoding="utf-8"))
    repos = corpus["repos"] if isinstance(corpus, dict) and "repos" in corpus else corpus
    if limit:
        repos = repos[:limit]

    client = GitHubClient(token)
    detector = PatternDetector(patterns_path=patterns_path, max_file_size_bytes=max_file_size_bytes)
    meta = RunMetadata(out_path, source="api", patterns_path=patterns_path,
                        repos_json_path=repos_json_path)

    done = already_processed_keys(out_path, key="repo_name")
    if done:
        print(f"Resuming: {len(done)} repos already in {out_path}, skipping those.")
        writer = IncrementalJSONArrayWriter.resume(out_path)
    else:
        writer = IncrementalJSONArrayWriter(out_path, mode="w")

    start = time.time()
    n_total = len(repos)
    n_scanned = n_failed = n_skipped_existing = 0

    try:
        for i, entry in enumerate(repos, 1):
            name = entry.get("name")
            if not name or name in done:
                n_skipped_existing += 1
                continue

            parsed = _parse_owner_repo(name)
            if parsed is None:
                writer.write(build_output_record(entry, None, status="failed",
                                                  error="could not parse owner/repo"))
                n_failed += 1
                print(f"[{i}/{n_total}] BAD NAME     {name}")
                continue
            owner, repo = parsed

            tar_bytes, err = fetch_tarball_bytes(client, owner, repo)
            if err:
                writer.write(build_output_record(entry, None, status="failed", error=err))
                n_failed += 1
                print(f"[{i}/{n_total}] FETCH FAILED {name}: {err}")
                continue

            try:
                scan_result = scan_tarball(detector, tar_bytes)
            except tarfile.TarError as e:
                writer.write(build_output_record(entry, None, status="failed",
                                                  error=f"tarball extract failed: {e}"))
                n_failed += 1
                print(f"[{i}/{n_total}] BAD TARBALL  {name}: {e}")
                continue
            finally:
                del tar_bytes

            writer.write(build_output_record(entry, scan_result, status="scanned"))
            n_scanned += 1
            print(f"[{i}/{n_total}] scanned      {name:45s} "
                  f"files={scan_result['files_scanned']:5d}  "
                  f"findings={len(scan_result['findings']):4d}")
            del scan_result

            if i % gc_every == 0:
                gc.collect()
    except KeyboardInterrupt:
        print("\nInterrupted -- closing output file cleanly so it stays valid JSON. "
              "Re-run the same command to resume.")
    finally:
        writer.close()

    elapsed = time.time() - start
    counts = {"scanned": n_scanned, "failed": n_failed,
              "skipped_already_done": n_skipped_existing, "elapsed_seconds": round(elapsed, 1)}
    meta.finish(counts)
    print(f"\nDone in {elapsed:.1f}s. scanned={n_scanned} failed={n_failed} "
          f"skipped(already done)={n_skipped_existing}")
    print(f"Wrote {out_path}")
    print(f"Wrote {meta.path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos_json", help="your corpus JSON (metadata + repos[] with frameworks)")
    ap.add_argument("out_json", nargs="?", default=None,
                     help="output path (default: toolkit/output/scan_results/pattern_scan_results_<UTC timestamp>.json)")
    ap.add_argument("--patterns", default=str(_DEFAULT_PATTERNS_PATH))
    ap.add_argument("--token", default=None, help="GitHub PAT (else reads GITHUB_TOKEN env var)")
    ap.add_argument("--max-file-size", type=int, default=2 * 1024 * 1024)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--gc-every", type=int, default=25)
    args = ap.parse_args()

    out_json = args.out_json or str(timestamped_results_path(_DEFAULT_RESULTS_DIR))

    run(args.repos_json, out_json, args.patterns, args.token,
        args.max_file_size, args.limit, args.gc_every)


if __name__ == "__main__":
    main()
