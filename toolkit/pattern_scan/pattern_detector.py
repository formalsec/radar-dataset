"""
pattern_detector.py (extended)

Adds four capabilities on top of the original detection logic, all additive —
existing fields (categories, flat_frameworks, named_agents) are unchanged in
shape and behavior. New capabilities:

1. CLASS-INHERITANCE RESOLUTION. `class SolidAssistantAgent(autogen.AssistantAgent):`
   followed by `SolidAssistantAgent(...)` is now recognized as an agent
   creation (same idea for rag_creation base classes -> store creation).
   Confirmed necessary by testing against a real repo (AI-Citizen/SolidGPT)
   where every actual agent was created via exactly this pattern.

2. named_stores: mirrors named_agents, but driven by the rag_creation
   category instead of agent_creation. This is the foundation the other
   three additions build on.

3. tools_bound + store links: at an agent-creation call site, keyword
   arguments are inspected for (a) `tools=[...]` -> tool names an agent can
   actually use (not just any tool-shaped object in the repo), and (b) a
   store-passing keyword (memory=/vector_store=/retriever=/knowledge=/store=)
   whose value is a Name matching an already-seen named_store -> a direct,
   single-file link between that store and this agent.

4. Coarse write taint check: for each rag_writes-category call, the first
   argument (unwrapping one level of list/tuple, since `.add(documents=[x])`
   is the dominant real shape) is traced back one assignment in the same
   function and checked against TAINT_SOURCE_MARKERS. This is a heuristic,
   NOT real dataflow propagation -- documented deliberately.

Everything above is single-file, single-hop. Cross-file store/agent linking
is aggregated at the repo level in scan_api.py / scan_local.py, using the
per-file named_agents/named_stores/store_agent_links this file now emits.
"""

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .pattern_index import MASTER_SET

CREATION_CATEGORIES = ("agent_creation", "rag_creation")

EXTENSION_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
}

DEFAULT_MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB

_JS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_JS_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

# Keyword arguments whose value, if it's a Name matching a known store
# variable, links that store to the agent being constructed. Not exhaustive
# -- covers the conventions actually seen across LangChain/CrewAI/AutoGen/
# LlamaIndex-style constructors. TODO: extend from real examples.
_STORE_LINK_KWARGS = ("memory", "vector_store", "vectorstore", "retriever", "knowledge", "store")

# One-hop taint markers -- keyword-based, NOT real taint tracking. See
# module docstring, point 4.
TAINT_SOURCE_MARKERS = [
    "request", "response", ".text", ".content", "http", "fetch",
    "tool_output", "tool_result", "retrieved", "retrieval",
    "user_input", "external", "web_content",
]
SANITIZER_KEYWORDS = ["validate", "sanitize", "clean", "escape", "schema", "pydantic"]


@dataclass
class FileResult:
    language: str | None
    parse_error: str | None = None
    categories: dict = field(default_factory=dict)
    flat_frameworks: dict = field(default_factory=dict)
    skipped_reason: str | None = None
    named_agents: list = field(default_factory=list)
    named_stores: list = field(default_factory=list)
    store_agent_links: list = field(default_factory=list)
    tainted_writes: list = field(default_factory=list)
    write_sites: list = field(default_factory=list)
    read_sites: list = field(default_factory=list)
    call_sites: list = field(default_factory=list)
    findings: list = field(default_factory=list)  # everything above, merged into one flat list


class CompiledCategory:
    """
    Precompiled regexes for one category's frameworks + generic buckets,
    PLUS a length-sorted merged list used for single-call attribution.

    Why the merged list is needed: several frameworks share overlapping
    patterns (e.g. Agno's bare "Agent(" is a plain substring of AutoGen's
    "AssistantAgent("). The per-category counting pass (_match_patterns)
    is fine checking these independently -- it counts hits per framework,
    not "which ONE framework does this call belong to." But
    _match_constructor_text (used for subclass-base resolution and
    single-call-site attribution) needs exactly one answer per call, and
    checking frameworks in alphabetical dict order let a short, generic
    pattern from an earlier-alphabetical framework win over a more
    specific pattern from a later one -- confirmed as a real bug via
    testing against AI-Citizen/SolidGPT (an AutoGen file, misattributed
    to Agno). Fix: check the MOST SPECIFIC (longest) pattern first,
    regardless of which framework it belongs to.
    """
    __slots__ = ("frameworks", "generic", "_ranked")

    def __init__(self, frameworks_raw, generic_raw):
        self.frameworks = {
            name: self._compile_all(patterns)
            for name, patterns in frameworks_raw.items()
        }
        self.generic = {
            name: self._compile_all(patterns)
            for name, patterns in generic_raw.items()
        }
        # Rank: (1) any real framework pattern beats any generic-bucket
        # pattern, ALWAYS -- a generic bucket is a deliberately broad
        # catch-all and should never outrank a framework-specific match
        # just because its regex text happens to be longer (e.g. the
        # generic "\\bAgent\\s*\\(" is textually longer than the
        # framework-specific "Agent(" despite being LESS specific).
        # (2) within the same tier, longer pattern text first, as a
        # reasonable specificity proxy (e.g. "AssistantAgent(" over "Agent(").
        # NOTE (known remaining limitation): two frameworks that share the
        # exact same literal pattern (e.g. Agno and CrewAI both listing
        # bare "Agent(") cannot be disambiguated by length at all -- that
        # tie is broken by insertion order today, which is not reliable.
        # Resolving that fully needs import-statement-based disambiguation
        # (checking which module "Agent" was imported from), not implemented
        # here yet -- flagged as a next step, not silently papered over.
        merged = []
        for label, compiled in self.frameworks.items():
            for pattern_str, regex in compiled:
                merged.append((pattern_str, label, regex, False))
        for label, compiled in self.generic.items():
            for pattern_str, regex in compiled:
                merged.append((pattern_str, label, regex, True))
        self._ranked = sorted(merged, key=lambda t: (t[3], -len(t[0])))

    @staticmethod
    def _compile_all(patterns):
        compiled = []
        for p in patterns:
            try:
                compiled.append((p, re.compile(p)))
            except re.error:
                compiled.append((p, re.compile(re.escape(p))))
        return compiled


class PatternDetector:
    def __init__(self, patterns_path="patterns_verified.json",
                 max_file_size_bytes=DEFAULT_MAX_FILE_SIZE_BYTES):
        raw = json.loads(Path(patterns_path).read_text(encoding="utf-8"))
        self.categories = {
            cat: CompiledCategory(data["frameworks"], data["generic"])
            for cat, data in raw["categories"].items()
        }
        self.max_file_size_bytes = max_file_size_bytes
        self._agent_creation_category = self.categories.get("agent_creation")
        self._rag_creation_category = self.categories.get("rag_creation")
        self._rag_writes_category = self.categories.get("rag_writes")
        self._rag_reads_category = self.categories.get("rag_reads")
        self._agent_calls_category = self.categories.get("agent_calls")

    def analyze(self, filepath):
        filepath = Path(filepath)
        language = EXTENSION_LANGUAGE_MAP.get(filepath.suffix.lower())
        if language is None:
            return FileResult(language=None, skipped_reason="unsupported extension")
        try:
            size = filepath.stat().st_size
        except OSError as e:
            return FileResult(language=language, skipped_reason=f"stat failed: {e}")
        if size > self.max_file_size_bytes:
            return FileResult(language=language, skipped_reason=f"file too large ({size} bytes)")
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return FileResult(language=language, skipped_reason=f"read failed: {e}")
        return self.analyze_source(source, filepath.name)

    def analyze_source(self, source, filename, size_bytes=None):
        language = EXTENSION_LANGUAGE_MAP.get(Path(filename).suffix.lower())
        if language is None:
            return FileResult(language=None, skipped_reason="unsupported extension")
        if size_bytes is not None and size_bytes > self.max_file_size_bytes:
            return FileResult(language=language, skipped_reason=f"file too large ({size_bytes} bytes)")

        if language == "python":
            reduced = _reduce_python(
                source, self._agent_creation_category,
                self._rag_creation_category, self._rag_writes_category,
                self._rag_reads_category, self._agent_calls_category,
            )
            (reduced_text, parse_error, named_agents, named_stores,
             store_links, tainted_writes, write_sites, read_sites, call_sites) = reduced
        else:
            reduced_text, parse_error = _reduce_javascript(source), None
            named_agents, named_stores, store_links, tainted_writes, write_sites, read_sites, call_sites = [], [], [], [], [], [], []

        result = FileResult(
            language=language, parse_error=parse_error,
            named_agents=named_agents, named_stores=named_stores,
            store_agent_links=store_links, tainted_writes=tainted_writes,
            write_sites=write_sites, read_sites=read_sites, call_sites=call_sites,
        )

        # ONE flat list, same shape for everything: what was found, what
        # text matched it, and where. This is the "good foundation" view --
        # meant for scanning by eye / spot-checking against the source,
        # not for programmatic aggregation (radar_summary is still
        # computed from the structured lists above, not from this).
        findings = []
        for a in named_agents:
            findings.append({
                "type": "agent", "name": a["name"], "framework": a["framework"],
                "matched": a.get("matched_call"), "line": a["line"],
            })
        for s in named_stores:
            findings.append({
                "type": "store", "name": s["variable"], "framework": s["framework"],
                "matched": s.get("matched_call"), "line": s["line"],
            })
        for c in call_sites:
            findings.append({
                "type": "call", "name": c["variable"], "framework": c.get("framework"),
                "matched": f".{c['method']}(", "line": c["line"],
            })
        for w in write_sites:
            findings.append({
                "type": "write", "name": w["variable"], "framework": w.get("framework"),
                "matched": f".{w['method']}(", "line": w["line"],
                "unsanitized": w.get("unsanitized"),
            })
        for r in read_sites:
            findings.append({
                "type": "read", "name": r["variable"], "framework": r.get("framework"),
                "matched": f".{r['method']}(", "line": r["line"],
            })
        findings.sort(key=lambda f: f["line"])
        result.findings = findings

        flat = {}

        # DEDUPLICATED categories: agent_creation, rag_creation, agent_calls,
        # rag_writes, rag_reads. Each call site was already resolved to
        # EXACTLY ONE framework (or None/"unattributed") during the AST
        # reduction above -- via _match_constructor_text's specificity
        # ranking for creations, and via named_agents/named_stores variable
        # lookup for calls/writes/reads. Counting from THOSE resolved sites,
        # instead of an independent regex sweep per framework, is what stops
        # a shared generic verb like ".add(" from incrementing every
        # framework that happens to list it (e.g. Chroma AND Mem0 both
        # getting +1 for one real Chroma call).
        if language == "python":
            for source_list, cat_name in (
                (named_agents, "agent_creation"), (named_stores, "rag_creation"),
                (call_sites, "agent_calls"), (write_sites, "rag_writes"), (read_sites, "rag_reads"),
            ):
                cat_out = {}
                for site in source_list:
                    fw = site.get("framework")
                    label = fw if fw else "unattributed"
                    cat_out.setdefault(label, {"count": 0, "patterns_matched": []})
                    cat_out[label]["count"] += 1
                    if fw:
                        flat[fw] = flat.get(fw, 0) + 1
                if cat_out:
                    result.categories[cat_name] = cat_out
            dedup_done = {"agent_creation", "rag_creation", "agent_calls", "rag_writes", "rag_reads"}
        else:
            dedup_done = set()

        # Everything else (a2a_interaction for Python; ALL categories for
        # JS/TS, which has no attribution machinery yet) keeps the original
        # independent-sweep counting. KNOWN LIMITATION, disclosed not
        # hidden: a2a_interaction and JS/TS results can still double-count
        # a pattern shared by multiple frameworks, same root cause as
        # before -- fixing that needs the same single-attribution treatment
        # extended to handoffs and to a JS/TS equivalent of named_agents/
        # named_stores, neither of which exist yet.
        for cat_name, compiled_cat in self.categories.items():
            if cat_name in dedup_done:
                continue
            cat_out = {}
            for fw_name, patterns in compiled_cat.frameworks.items():
                count, matched = _match_patterns(patterns, reduced_text)
                if count:
                    cat_out[fw_name] = {"count": count, "patterns_matched": matched}
                    flat[fw_name] = flat.get(fw_name, 0) + count
            for label, patterns in compiled_cat.generic.items():
                count, matched = _match_patterns(patterns, reduced_text)
                if count:
                    cat_out[label] = {"count": count, "patterns_matched": matched}
            if cat_out:
                result.categories[cat_name] = cat_out

        result.flat_frameworks = flat
        return result

    def analyze_file_flat(self, filepath):
        result = self.analyze(filepath)
        return result.language, result.flat_frameworks


def _match_patterns(compiled_patterns, text):
    total = 0
    matched_names = []
    for pattern_str, regex in compiled_patterns:
        hits = regex.findall(text)
        if hits:
            total += len(hits)
            matched_names.append(pattern_str)
    return total, matched_names


# ---------------------------------------------------------------------- #
# Python: ast-based reduction, now with class-inheritance resolution,
# named_stores, store/agent linking, and a one-hop taint check.
# ---------------------------------------------------------------------- #

def _resolve_expr_text(node):
    """Best-effort text for a base-class expression, e.g. autogen.AssistantAgent."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _match_constructor_text(call_or_base_text, category):
    """Most SPECIFIC (longest pattern) match wins, not the alphabetically
    first framework -- see CompiledCategory docstring for why this matters."""
    if category is None:
        return None
    for pattern_str, label, regex, _is_generic in category._ranked:
        if regex.search(call_or_base_text):
            return label
    return None


def _build_subclass_extensions(tree, agent_creation_category, rag_creation_category):
    """
    Detects `class X(KnownBase):` for both agent_creation and rag_creation
    bases. Returns (extra_agent_classes, extra_store_classes), each a dict
    {class_name: framework}, to be checked ALONGSIDE (not instead of) the
    normal compiled-pattern matching for every Call site in this file.
    Single-hop: a subclass of a subclass is not resolved.
    """
    extra_agents, extra_stores = {}, {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_text = _resolve_expr_text(base) + "("
            fw = _match_constructor_text(base_text, agent_creation_category)
            if fw is not None:
                extra_agents[node.name] = fw
                continue
            fw = _match_constructor_text(base_text, rag_creation_category)
            if fw is not None:
                extra_stores[node.name] = fw
    return extra_agents, extra_stores


_NAME_KWARGS = ("name", "role", "id", "agent_id")


def _extract_name_kwarg(call_node):
    for kw in call_node.keywords:
        if kw.arg in _NAME_KWARGS and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _extract_simple_target_name(targets):
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def _extract_tools_bound(call_node):
    """`Agent(tools=[search_tool, calc_tool])` -> ["search_tool", "calc_tool"].
    Attribution-gated tool counting: a tool only counts if it actually shows
    up here, not just anywhere in the repo."""
    for kw in call_node.keywords:
        if kw.arg == "tools" and isinstance(kw.value, (ast.List, ast.Tuple)):
            return [elt.id for elt in kw.value.elts if isinstance(elt, ast.Name)]
    return []


def _extract_store_link(call_node, known_store_vars):
    """If a store-passing keyword's value is a Name matching a known store
    variable, return that variable name (the link), else None."""
    for kw in call_node.keywords:
        if kw.arg in _STORE_LINK_KWARGS and isinstance(kw.value, ast.Name):
            if kw.value.id in known_store_vars:
                return kw.value.id
    return None


def _first_call_argument(call_node):
    if call_node.args:
        return call_node.args[0]
    if call_node.keywords:
        return call_node.keywords[0].value
    return None


def _is_write_argument_tainted(call_node, assigns_by_func_and_name, func_ctx):
    """One-hop taint check on a rag_writes call's first argument, unwrapping
    one level of list/tuple (since `.add(documents=[x])` is the dominant
    real shape). NOT real dataflow propagation -- see module docstring."""
    arg_node = _first_call_argument(call_node)
    if arg_node is None:
        return False, None

    candidates = arg_node.elts if isinstance(arg_node, (ast.List, ast.Tuple)) else [arg_node]
    for candidate in candidates:
        if isinstance(candidate, ast.Name):
            source_expr = assigns_by_func_and_name.get((func_ctx, candidate.id))
            source_text = _resolve_expr_text(source_expr) if source_expr is not None else candidate.id
        else:
            source_text = _resolve_expr_text(candidate)
        lowered = source_text.lower()
        for marker in TAINT_SOURCE_MARKERS:
            if marker in lowered:
                return True, marker
    return False, None


def _has_sanitizer_nearby(source_lines, line_no, window=5):
    start = max(0, line_no - window)
    context = " ".join(source_lines[start:line_no]).lower()
    return any(kw in context for kw in SANITIZER_KEYWORDS)


def _walk_with_function_context(node, current_function=None):
    """Like ast.walk, but also yields the enclosing function name (or None
    at module level) -- what makes single-hop, same-function taint lookback
    possible without a real call graph."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        current_function = node.name
    yield node, current_function
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_function_context(child, current_function)


def _reduce_python(source, agent_creation_category, rag_creation_category, rag_writes_category, rag_reads_category=None, agent_calls_category=None):
    """
    Returns (reduced_text, parse_error, named_agents, named_stores,
    store_agent_links, tainted_writes).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        return source, f"{type(e).__name__}: {e}", [], [], [], [], [], [], [], []
    except RecursionError as e:
        return source, f"RecursionError: {e}", [], [], [], [], [], [], [], []

    source_lines = source.splitlines()
    extra_agent_classes, extra_store_classes = _build_subclass_extensions(
        tree, agent_creation_category, rag_creation_category
    )

    lines = []
    pending_agent_calls = {}
    pending_store_calls = {}
    assign_target_for_call = {}
    assigns_by_func_and_name = {}  # (func_ctx, var_name) -> most recent Assign.value node

    # Pass 1: imports/decorators/literals/calls -> reduced text, plus collect
    # every simple Assign so the taint check can look up "most recent source"
    # after the full walk (order-independent, same idea as the existing
    # assign_target_for_call merge-after-walk pattern).
    for node, func_ctx in _walk_with_function_context(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                lines.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                lines.append(f"from {module} import {alias.name}")
        elif isinstance(node, ast.Call):
            try:
                func_repr = ast.unparse(node.func)
            except Exception:
                continue
            call_text = f"{func_repr}("
            lines.append(call_text)

            # Agent creation: normal compiled patterns, OR a subclass name
            # resolved via _build_subclass_extensions.
            framework = None
            if agent_creation_category is not None:
                framework = _match_constructor_text(call_text, agent_creation_category)
            if framework is None and isinstance(node.func, ast.Name):
                framework = extra_agent_classes.get(node.func.id)
            if framework is not None:
                pending_agent_calls[id(node)] = {
                    "framework": framework,
                    "name": _extract_name_kwarg(node),
                    "line": node.lineno,
                    "tools_bound": _extract_tools_bound(node),
                    "call_node": node,
                    "matched_call": call_text,  # e.g. "SolidAssistantAgent(" -- so you can eyeball-confirm the match
                }
                continue  # a call site is either an agent or a store, not both

            # Store creation: same idea, for rag_creation.
            store_framework = None
            if rag_creation_category is not None:
                store_framework = _match_constructor_text(call_text, rag_creation_category)
            if store_framework is None and isinstance(node.func, ast.Name):
                store_framework = extra_store_classes.get(node.func.id)
            if store_framework is None and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                # classmethod factory shape, e.g. VectorStoreIndex.from_documents(...)
                store_framework = _match_constructor_text(call_text, rag_creation_category)
            if store_framework is not None:
                pending_store_calls[id(node)] = {
                    "framework": store_framework,
                    "line": node.lineno,
                    "call_node": node,
                    "matched_call": call_text,
                }

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                try:
                    lines.append(f"@{ast.unparse(dec)}")
                except Exception:
                    continue
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lines.append(node.value)
        elif isinstance(node, ast.Assign):
            var_name = _extract_simple_target_name(node.targets)
            if var_name is not None:
                if isinstance(node.value, ast.Call):
                    assign_target_for_call[id(node.value)] = var_name
                assigns_by_func_and_name[(func_ctx, var_name)] = node.value

    # Resolve agent/store variable names from their assignment (fallback to
    # the assigned variable name when there's no name=/role=/id= kwarg).
    named_agents = []
    agent_var_by_call_id = {}
    agent_framework_by_var = {}  # populated below, used by call_sites/write/read attribution
    for call_id, entry in pending_agent_calls.items():
        var_name = assign_target_for_call.get(call_id)
        if entry["name"] is None:
            entry["name"] = var_name
        agent_var_by_call_id[call_id] = var_name
        named_agents.append({
            "name": entry["name"], "framework": entry["framework"],
            "line": entry["line"], "tools_bound": entry["tools_bound"],
            "matched_call": entry["matched_call"],
        })
    agent_framework_by_var.update({
        var_name: entry["framework"]
        for call_id, entry in pending_agent_calls.items()
        for var_name in [agent_var_by_call_id[call_id]] if var_name
    })

    named_stores = []
    store_var_by_call_id = {}
    for call_id, entry in pending_store_calls.items():
        var_name = assign_target_for_call.get(call_id)
        store_var_by_call_id[call_id] = var_name
        named_stores.append({
            "variable": var_name, "framework": entry["framework"], "line": entry["line"],
            "matched_call": entry["matched_call"],
        })
    known_store_vars = {s["variable"] for s in named_stores if s["variable"]}
    # variable -> single resolved framework, used to deduplicate write/read
    # counting below (was previously counted independently per framework
    # whenever a shared verb like ".add(" matched, regardless of which
    # actual store the call was on).
    store_framework_by_var = {s["variable"]: s["framework"] for s in named_stores if s["variable"]}

    # store_agent_links: direct kwarg-passing at agent-construction time,
    # e.g. Agent(memory=kb) where `kb` is a known store variable.
    store_agent_links = []
    for call_id, entry in pending_agent_calls.items():
        linked_store = _extract_store_link(entry["call_node"], known_store_vars)
        if linked_store is not None:
            store_agent_links.append({
                "store": linked_store,
                "agent": entry["name"] or agent_var_by_call_id.get(call_id),
                "line": entry["line"],
            })

    # write_sites: EVERY rag_writes-category call site, with its receiver
    # variable -- needed for rag_writers/rag_readers, which are about ALL
    # writes/reads, not just tainted ones. tainted_writes was originally
    # only recording the tainted subset, which silently dropped every
    # non-risky write from the output entirely -- fixed to always record
    # the site, with taint info attached as extra fields on top.
    write_sites = []
    if rag_writes_category is not None:
        for node, func_ctx in _walk_with_function_context(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            method_text = f".{node.func.attr}("
            if _match_constructor_text(method_text, rag_writes_category) is None:
                continue
            receiver = node.func.value.id if isinstance(node.func.value, ast.Name) else None
            tainted, source = _is_write_argument_tainted(node, assigns_by_func_and_name, func_ctx)
            sanitized_nearby = _has_sanitizer_nearby(source_lines, node.lineno) if tainted else False
            write_sites.append({
                "line": node.lineno, "variable": receiver, "method": node.func.attr,
                "framework": store_framework_by_var.get(receiver),  # single resolved framework, or None if unattributed
                "tainted": tainted, "taint_source": source,
                "sanitizer_nearby": sanitized_nearby,
                "unsanitized": tainted and not sanitized_nearby,
            })
    tainted_writes = [w for w in write_sites if w["unsanitized"]]

    # read_sites: same idea as write_sites but for rag_reads -- no taint
    # check (reads don't have a write-time "was this checked" question the
    # same way; a read's risk shows up in rag_influences_actions instead,
    # not modeled here).
    read_sites = []
    if rag_reads_category is not None:
        for node, func_ctx in _walk_with_function_context(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method_text = f".{node.func.attr}("
            if _match_constructor_text(method_text, rag_reads_category) is None:
                continue
            receiver = node.func.value.id if isinstance(node.func.value, ast.Name) else None
            read_sites.append({
                "line": node.lineno, "variable": receiver, "method": node.func.attr,
                "framework": store_framework_by_var.get(receiver),
            })

    # call_sites: agent_calls-category call sites, attributed to a single
    # resolved framework via named_agents -- same deduplication idea as
    # write_sites/read_sites above.
    call_sites = []
    if agent_calls_category is not None:
        for node, func_ctx in _walk_with_function_context(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method_text = f".{node.func.attr}("
            if _match_constructor_text(method_text, agent_calls_category) is None:
                continue
            receiver = node.func.value.id if isinstance(node.func.value, ast.Name) else None
            call_sites.append({
                "line": node.lineno, "variable": receiver, "method": node.func.attr,
                "framework": agent_framework_by_var.get(receiver),
            })

    return ("\n".join(lines), None, named_agents, named_stores,
            store_agent_links, tainted_writes, write_sites, read_sites, call_sites)


def _reduce_javascript(source):
    text = _JS_BLOCK_COMMENT_RE.sub(" ", source)
    text = _JS_LINE_COMMENT_RE.sub(" ", text)
    return text


def apply_framework_confirmation(categories):
    confirmed = set()
    for cat in CREATION_CATEGORIES:
        for label in categories.get(cat, {}):
            if label in MASTER_SET:
                confirmed.add(label)
    for cat, cat_hits in categories.items():
        if cat in CREATION_CATEGORIES:
            continue
        for label, entry in cat_hits.items():
            if label in MASTER_SET:
                entry["confirmed"] = label in confirmed
    return confirmed