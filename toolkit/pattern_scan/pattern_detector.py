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

# Maps a Python import's module root to the framework it belongs to. This
# is the grounded, "checked against what's actually true" resolution --
# preferred over pattern-specificity guessing whenever it applies. Fixes
# the one tie pattern-length ranking genuinely cannot break: two
# frameworks listing the EXACT SAME bare pattern (Agno and CrewAI both
# list plain "Agent(") can only be told apart by knowing which module the
# name was actually imported from.
MODULE_TO_FRAMEWORK = {
    "crewai": "CrewAI",
    "autogen": "AutoGen", "pyautogen": "AutoGen",
    "agno": "Agno",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "llama_index": "LlamaIndex",
    "haystack": "Haystack",
    "pydantic_ai": "Pydantic AI",
    "smolagents": "Smolagents",
    "agents": "OpenAI Agents SDK",
    "beeai_framework": "Bee Agent Framework", "bee_agent_framework": "Bee Agent Framework",
    "deepagents": "Deep Agents",
    "camel": "CAMEL",
    "chromadb": "Chroma",
    "qdrant_client": "Qdrant",
    "pinecone": "Pinecone",
    "weaviate": "Weaviate",
    "pgvector": "PGVector",
    "pymilvus": "Milvus",
    "mem0": "Mem0",
    "zep_python": "Zep", "zep_cloud": "Zep",
    "openai": "OpenAI SDK",
    "anthropic": "Anthropic SDK",
    "claude_agent_sdk": "Claude Agent SDK",
    "claude_code_sdk": "Claude Agent SDK",
    "google": "Google GenAI",  # covers google.genai / google.generativeai -- see MODULE_SUBPATH_TO_FRAMEWORK for google.adk
    "together": "Together SDK",
    "groq": "Groq SDK",
    "mistralai": "Mistral SDK",
    "instructor": "Instructor",
    "playwright": "Playwright",
    "browser_use": "Browser-use",
    "e2b": "E2B",
    "composio": "Composio",
    "mcp": "MCP SDK",
    "fastmcp": "FastMCP",
    "a2a": "A2A SDK", "a2a_sdk": "A2A SDK",
    # TODO: extend as real repos surface more import styles not covered here.
}

# Submodule-level overrides, checked BEFORE MODULE_TO_FRAMEWORK's single
# top-level-segment lookup -- needed when a specific submodule belongs to a
# DIFFERENT framework than its top-level package's default mapping (e.g.
# `google.adk` is Google's Agent Development Kit, not Google GenAI, even
# though both import under plain `google`).
MODULE_SUBPATH_TO_FRAMEWORK = {
    "google.adk": "Google ADK",
}


def _resolve_module_framework(canonical):
    """
    `canonical` is the FULL dotted import path (e.g.
    "google.adk.agents.Agent", "browser_use", "langchain_classic.agents"),
    not just its first segment -- needed so a submodule can be resolved
    differently from its top-level package (see MODULE_SUBPATH_TO_FRAMEWORK).

    Falls back to MODULE_TO_FRAMEWORK.get(module_root), plus a prefix rule
    for LangChain's own package split: separate PyPI/import roots per
    integration (langchain_openai, langchain_anthropic, langchain_mistralai,
    langchain_classic, langchain_community, ...) that all still ship as
    part of the LangChain framework. Confirmed a real gap by testing: `from
    langchain_classic.agents import AgentExecutor, create_tool_calling_agent`
    left both calls completely unconfirmed, because only bare "langchain"
    was ever mapped. A prefix rule (vs. hardcoding each package name) covers
    future langchain_* splits the same way.
    """
    parts = canonical.split(".")
    if len(parts) > 1:
        subpath_fw = MODULE_SUBPATH_TO_FRAMEWORK.get(f"{parts[0]}.{parts[1]}")
        if subpath_fw is not None:
            return subpath_fw
    module_root = parts[0]
    fw = MODULE_TO_FRAMEWORK.get(module_root)
    if fw is not None:
        return fw
    if module_root.startswith("langchain_"):
        return "LangChain"
    return None


def _build_import_aliases(tree):
    """
    local_name -> canonical "module.Symbol" (or just "module" for a bare
    `import module`). Examples:
        from crewai import Agent          -> {"Agent": "crewai.Agent"}
        import autogen                     -> {"autogen": "autogen"}
        import autogen as ag               -> {"ag": "autogen"}
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = f"{module}.{alias.name}" if module else alias.name
    return aliases

import json
import posixpath
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

DEFAULT_MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # Mudar

_JS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_JS_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

# Keyword arguments whose value, if it's a Name matching a known store
# variable, links that store to the agent being constructed. Not exhaustive
# -- covers the conventions actually seen across LangChain/CrewAI/AutoGen/
# LlamaIndex-style constructors. TODO: extend from real examples.
_STORE_LINK_KWARGS = ("memory", "vector_store", "vectorstore", "retriever", "knowledge", "store")

# Modules whose own `.compile(` method has nothing to do with an agent
# graph (re.compile, py_compile.compile, ...) but share the generic
# "Graph Compile" pattern's bare `.compile(` text. TODO: extend as real
# repos surface more such collisions.
_NON_AGENT_COMPILE_MODULES = {"re", "regex", "py_compile", "_re"}

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
    imports: list = field(default_factory=list)  # every import in the file + resolved framework, if any
    llm_tool_calls: list = field(default_factory=list)  # Option A: confirmed OpenAI/Anthropic tools= calls
    tool_use_markers: list = field(default_factory=list)  # custom tool-registration markers (line-located)
    agent_markers: list = field(default_factory=list)  # custom agent-abstraction markers (line-located)
    tool_definition_markers: list = field(default_factory=list)  # Unconfirmed definition markers
    custom_agents: list = field(default_factory=list)  # confirmed hand-rolled agents, see _group_custom_agents
    confirmed_tool_definitions: list = field(default_factory=list)  # Confirmed Python tool definitions


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
        #
        # Two frameworks sharing the EXACT same literal pattern (Agno and
        # CrewAI both list a bare "Agent(") cannot be told apart by length
        # at all -- ranking alone would break that tie by insertion order,
        # which is meaningless. That case is handled downstream instead, by
        # _frameworks_for_call_identifier + _match_constructor_text: the
        # call's own name is resolved through THIS FILE's import statements,
        # so `from crewai import Agent` settles it as CrewAI and
        # `from agno.agent import Agent` as Agno. Ranking narrows the
        # candidates; imports confirm the answer.
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
        # language(s) each framework's patterns were written for; absent = unrestricted
        self._framework_languages = {
            cat: data.get("framework_languages", {})
            for cat, data in raw["categories"].items()
        }
        self.max_file_size_bytes = max_file_size_bytes
        self._agent_creation_category = self.categories.get("agent_creation")
        self._rag_creation_category = self.categories.get("rag_creation")
        self._rag_writes_category = self.categories.get("rag_writes")
        self._rag_reads_category = self.categories.get("rag_reads")
        self._agent_calls_category = self.categories.get("agent_calls")
        self._tool_definition_category = self.categories.get("tool_definition")

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

    def analyze_source(self, source, filename, size_bytes=None, external_exports=None,
                       javascript_sources=None):
        """
        external_exports: optional {"stores": {module: {var: framework}},
        "agents": {module: {var: framework}}} collected from a FIRST pass
        over the repo, letting this file resolve a store OR agent it
        imported from another file (`from store import kb`,
        `from agents import researcher`) -- without it, a write/read/call
        performed in a different file from where the object was created is
        invisible, which systematically under-counts rag_writers,
        rag_readers and agent calls in modular codebases.
        See scan_api._collect_exports for how it's built.
        """
        language = EXTENSION_LANGUAGE_MAP.get(Path(filename).suffix.lower())
        if language is None:
            return FileResult(language=None, skipped_reason="unsupported extension")
        if size_bytes is not None and size_bytes > self.max_file_size_bytes:
            return FileResult(language=language, skipped_reason=f"file too large ({size_bytes} bytes)")

        source_lines = source.splitlines()
        tool_definition_markers = _detect_tool_definition_markers(source_lines)
        if language == "python":
            reduced = _reduce_python(
                source, self._agent_creation_category,
                self._rag_creation_category, self._rag_writes_category,
                self._rag_reads_category, self._agent_calls_category,
                self._tool_definition_category,
                external_exports=external_exports,
            )
            (reduced_text, parse_error, named_agents, named_stores,
             store_links, tainted_writes, write_sites, read_sites, call_sites, imports,
             llm_tool_calls, tool_use_markers, agent_markers,
             confirmed_tool_definitions) = reduced
        else:
            reduced_text, parse_error = _reduce_javascript(source), None
            named_stores, store_links, tainted_writes, write_sites, read_sites, call_sites, imports = [], [], [], [], [], [], []
            # No AST reduction for JS/TS -- plain-text detectors only.
            named_agents = _detect_js_named_agents(
                source_lines, self._agent_creation_category,
                self._framework_languages.get("agent_creation", {}),
                filename, javascript_sources,
            )
            llm_tool_calls = _detect_bind_tools_calls(source_lines)
            tool_use_markers = _detect_tool_use_markers(source_lines)
            agent_markers = _detect_agent_markers(source_lines)
            confirmed_tool_definitions = (
                _detect_webmcp_tool_definitions(source_lines, filename, javascript_sources)
                + _detect_js_registry_tool_definitions(source_lines, filename, javascript_sources)
                + _detect_js_mcp_server_tool_definitions(source_lines)
            )

        result = FileResult(
            language=language, parse_error=parse_error,
            named_agents=named_agents, named_stores=named_stores,
            store_agent_links=store_links, tainted_writes=tainted_writes,
            write_sites=write_sites, read_sites=read_sites, call_sites=call_sites,
            imports=imports, llm_tool_calls=llm_tool_calls,
            tool_use_markers=tool_use_markers, agent_markers=agent_markers,
            tool_definition_markers=tool_definition_markers,
            custom_agents=_group_custom_agents(llm_tool_calls),
            confirmed_tool_definitions=confirmed_tool_definitions,
        )

        # ONE flat list, same shape for everything: what was found, what
        # text matched it, and where. This is the "good foundation" view --
        # meant for scanning by eye / spot-checking against the source,
        # not for programmatic aggregation (radar_summary is still
        # computed from the structured lists above, not from this).
        def _line_content(lineno):
            return source_lines[lineno - 1].strip() if 0 < lineno <= len(source_lines) else None

        findings = []
        for a in named_agents:
            findings.append({
                "type": "agent", "name": a["name"], "framework": a["framework"],
                "matched": a.get("matched_call"), "line": a["line"],
                "line_content": _line_content(a["line"]), "tools_bound": a.get("tools_bound"),
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
        # tool_use, from two sources, both located by line so they can be
        # spot-checked the same way as everything else:
        #   1. confirmed: tools= handed to a real OpenAI/Anthropic client
        #   2. marker: a custom tool-registration convention (no framework
        #      import to confirm it against -- framework stays null)
        for t in llm_tool_calls:
            findings.append({
                "type": "tool_use", "name": t["variable"], "framework": t["framework"],
                "matched": "tools=", "line": t["line"],
                "line_content": _line_content(t["line"]), "tool_names": t.get("tool_names"),
            })
        for m in tool_use_markers:
            findings.append({
                "type": "tool_use", "name": None, "framework": None,
                "matched": m["matched"], "line": m["line"],
                "line_content": _line_content(m["line"]),
            })
        # agent_marker: same idea as the tool_use markers above, for repos
        # whose agent abstraction is hand-rolled (no tracked framework
        # constructor to confirm against). framework stays null -- this is
        # a located observation, never a confirmed agent, and it does NOT
        # feed n_agents.
        for m in agent_markers:
            findings.append({
                "type": "agent_marker", "name": None, "framework": None,
                "matched": m["matched"], "line": m["line"],
                "line_content": _line_content(m["line"]),
            })
        for m in tool_definition_markers:
            findings.append({
                "type": "tool_definition", "name": None, "framework": None,
                "matched": m["matched"], "line": m["line"],
                "line_content": _line_content(m["line"]),
            })
        for t in confirmed_tool_definitions:
            findings.append({
                "type": "confirmed_tool_definition", "name": t["name"], "framework": t["framework"],
                "matched": t.get("matched_call"), "line": t["line"],
                "line_content": _line_content(t["line"]),
            })
        # custom_agent: a hand-rolled agent counted separately from
        # framework-confirmed ones (n_custom_agents in scan_api.py, not
        # n_agents) -- confirmed only via real tool-calling evidence
        # (result.llm_tool_calls), never a guess.
        for c in result.custom_agents:
            findings.append({
                "type": "custom_agent", "name": c["variable"], "framework": c["framework"],
                "matched": c.get("matched_call"), "line": c["line"],
                "line_content": _line_content(c["line"]), "tool_names": c.get("tool_names"),
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
            allowed_langs_by_fw = self._framework_languages.get(cat_name, {})
            cat_out = {}
            for fw_name, patterns in compiled_cat.frameworks.items():
                allowed_langs = allowed_langs_by_fw.get(fw_name)
                if allowed_langs is not None and language not in allowed_langs:
                    continue
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


def _unwrap_subscript_callee(func_node):
    """Return the constructor behind a parameterized generic."""
    if isinstance(func_node, ast.Subscript):
        return func_node.value
    return func_node


def _callee_simple_name(func_node):
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def _match_constructor_text(call_or_base_text, category, imported_frameworks=None, require_confirmation=True):
    """
    Two different uses need two different strictness levels here:

    1. IDENTITY resolution (require_confirmation=True, the default) --
       "which framework is this creation?" A framework-specific pattern is
       only trusted if the file's own imports actually confirm it. If
       nothing confirms a specific framework, this returns the best
       GENERIC match instead (a generic label like "Direct Agent" isn't a
       wrong specific guess -- it's honestly saying "something agent-shaped
       was found, framework unconfirmed"). If there's no generic match
       either, returns None rather than guessing a specific framework with
       no evidence behind it.

    2. ROLE-ONLY checks (require_confirmation=False) -- used by the
       write/read/call detection below, where this function's return value
       is only checked with `is None` to decide "does this call even look
       like a write/read/call at all." The actual framework for those
       comes from a completely separate mechanism (looking up which
       variable the call was made on, via store_framework_by_var /
       agent_framework_by_var) -- so applying import-confirmation HERE was
       a real bug: it caused every write/read/call whose verb only exists
       under a framework-specific row (not a generic one) to stop
       registering as a write/read/call at all. Confirmed by testing
       (`kb.add(...)` after `from chromadb import Chroma` stopped showing
       up in write_sites once this function got stricter). Fixed by
       skipping the confirmation requirement for this use case -- it never
       needed it in the first place.

    Trade-off, stated plainly, for use case 1: if MODULE_TO_FRAMEWORK
    doesn't yet cover a framework's real import name, a genuine agent/store
    using that framework will now go undetected here rather than being
    (possibly wrongly) guessed. Deliberate choice: "say nothing" over
    "confidently name the wrong framework."
    """
    if category is None:
        return None
    if not require_confirmation:
        for pattern_str, label, regex, _is_generic in category._ranked:
            if regex.search(call_or_base_text):
                return label
        return None
    generic_match = None
    for pattern_str, label, regex, is_generic in category._ranked:
        if not regex.search(call_or_base_text):
            continue
        if not is_generic:
            if imported_frameworks and label in imported_frameworks:
                return label  # confirmed by import -- highest confidence, stop here
            continue  # unconfirmed specific-framework match -- do NOT guess it
        if generic_match is None:
            generic_match = label
    return generic_match


def _build_subclass_extensions(tree, agent_creation_category, rag_creation_category, import_aliases):
    """
    Detects `class X(KnownBase):` for both agent_creation and rag_creation
    bases. Returns (extra_agent_classes, extra_store_classes), each a dict
    {class_name: framework}, to be checked ALONGSIDE (not instead of) the
    normal compiled-pattern matching for every Call site in this file.
    Single-hop: a subclass of a subclass is not resolved.

    Uses the same per-identifier confirmation as the main call resolution
    (see _frameworks_for_call_identifier) -- a class base is only
    confirmed against the framework IT specifically was imported from, not
    "any framework imported anywhere in this file."
    """
    extra_agents, extra_stores = {}, {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_text = _resolve_expr_text(base) + "("
            base_frameworks = _frameworks_for_call_identifier(base, import_aliases)
            # Same canonical-form fallback as the main call resolution, so a
            # renamed import used as a base class (`from autogen import
            # AssistantAgent as AA` then `class X(AA):`) still matches a
            # qualified registry pattern.
            canonical_base_text = _canonical_call_text(base, import_aliases)
            fw = _match_constructor_text(base_text, agent_creation_category, base_frameworks)
            if fw is None and canonical_base_text:
                fw = _match_constructor_text(canonical_base_text, agent_creation_category, base_frameworks)
            if fw is not None:
                extra_agents[node.name] = fw
                continue
            fw = _match_constructor_text(base_text, rag_creation_category, base_frameworks)
            if fw is None and canonical_base_text:
                fw = _match_constructor_text(canonical_base_text, rag_creation_category, base_frameworks)
            if fw is not None:
                extra_stores[node.name] = fw
    return extra_agents, extra_stores


_NAME_KWARGS = ("name", "role", "id", "agent_id")


def _extract_name_kwarg(call_node):
    for kw in call_node.keywords:
        if kw.arg in _NAME_KWARGS and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _resolve_identity(node):
    """
    Resolves 'the thing this belongs to' to a single identity string,
    walking down through however many chained attributes sit on top of the
    base. Handles three shapes with one algorithm:
      - a bare variable (`kb` -> "kb")
      - an instance attribute assignment/receiver (`self.client` -> "self.client")
      - a multi-segment SDK namespace chain with a bare base
        (`client.chat.completions.create` -> "client", ignoring the
        `.chat.completions.create` traversal entirely)
      - the same chain with an instance-attribute base
        (`self.client.chat.completions.create` -> "self.client")

    The key distinction: if the walk bottoms out at a bare Name that ISN'T
    `self`, that Name alone is the real identity -- everything above it
    was just namespace traversal (`.chat.completions...`), not a separate
    binding. If it bottoms out at `self`, `self` alone is never a
    registered variable -- the ONE attribute immediately on top of it
    (`self.client`) is the actual bound identity from a `self.client = ...`
    assignment; anything further above that (`.chat.completions...`) is
    still just traversal.

    Confirmed as a real, broad gap by testing: `self.client = OpenAI()`
    followed by `self.client.chat.completions.create(...)` previously went
    completely unattributed, because only a bare `Name` was ever
    recognized -- and assigning a creation to `self.attr` is one of the
    most common patterns in real object-oriented agent code (confirmed in
    NousResearch/hermes-agent's actual AIAgent class).
    """
    chain = []
    while isinstance(node, ast.Attribute):
        chain.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    if node.id == "self" and chain:
        return f"self.{chain[-1]}"  # innermost attribute on self = the actual bound identity
    return node.id


def _extract_simple_target_name(targets):
    if len(targets) != 1:
        return None
    return _resolve_identity(targets[0])


def _wrapping_call_framework(call_node, var_name, prelim_framework_by_var):
    """True when `call_node` is a method call (`X.method(...)`) on a
    receiver that's ALREADY a known creation of the SAME kind in this file
    -- e.g. `app = workflow.compile()` after `workflow = StateGraph(...)`
    is the same agent finalized, not a second one. Returns the receiver's
    framework, or None. A bare-name creation call is never an Attribute
    call and is untouched by this check."""
    func = call_node.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = _resolve_identity(func)
    if receiver is None or receiver == var_name:
        return None
    return prelim_framework_by_var.get(receiver)


def _tool_element_identifier(elt, import_aliases=None):
    """Best-effort identifier for one element of a `tools=[...]` list: a bare
    name (`search_tool`) or a tool-factory call (`get_search_ddg_tool()`) --
    the latter is the dominant real shape for repos that build each Tool via
    a small wrapper function, confirmed in naotaka1128/web_bowsing_agent's
    `tools = [get_search_ddg_tool(), get_fetch_page_tool()]`."""
    if isinstance(elt, ast.Name):
        return elt.id
    if isinstance(elt, ast.Attribute):
        return elt.attr
    if isinstance(elt, ast.Call):
        canonical = _canonical_call_text(elt.func, import_aliases or {}) or ""
        if canonical.startswith("camel.toolkits.") and canonical.endswith(".FunctionTool("):
            wrapped = elt.args[0] if elt.args else next(
                (kw.value for kw in elt.keywords if kw.arg == "func"), None)
            return _tool_element_identifier(wrapped, import_aliases)
        func = elt.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _dict_value_for_key(dict_node, key):
    """Look up a string key in a literal dictionary."""
    if not isinstance(dict_node, ast.Dict):
        return None
    for k, v in zip(dict_node.keys, dict_node.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


_POSITIONAL_TOOLS_ARG_INDEX = {
    "create_react_agent": 1, "create_openai_tools_agent": 1,
    "create_tool_calling_agent": 1, "create_structured_chat_agent": 1,
    "initialize_agent": 0,
}


def _resolve_tools_value(value, assigns_by_func_and_name, func_ctx, import_aliases=None):
    """Resolve a local tool list or tuple through one assignment."""
    if isinstance(value, ast.Name) and assigns_by_func_and_name is not None:
        value = assigns_by_func_and_name.get((func_ctx, value.id), value)
    if isinstance(value, (ast.List, ast.Tuple)):
        return [name for name in (_tool_element_identifier(e, import_aliases) for e in value.elts) if name]
    return None


def _extract_tools_bound(call_node, assigns_by_func_and_name=None, func_ctx=None, callee_name=None,
                         import_aliases=None):
    """Extract tools from keywords, literal kwargs, or known positional arguments."""
    for kw in call_node.keywords:
        if kw.arg == "tools":
            names = _resolve_tools_value(kw.value, assigns_by_func_and_name, func_ctx, import_aliases)
            if names is not None:
                return names
        elif kw.arg is None:
            source = kw.value
            if isinstance(source, ast.Name) and assigns_by_func_and_name is not None:
                source = assigns_by_func_and_name.get((func_ctx, source.id))
            names = _resolve_tools_value(_dict_value_for_key(source, "tools"),
                                          assigns_by_func_and_name, func_ctx, import_aliases)
            if names is not None:
                return names

    idx = _POSITIONAL_TOOLS_ARG_INDEX.get(callee_name)
    if idx is not None and len(call_node.args) > idx:
        names = _resolve_tools_value(call_node.args[idx], assigns_by_func_and_name, func_ctx, import_aliases)
        if names is not None:
            return names
    return []


def _extract_bind_tools_names(call_node):
    """`model.bind_tools([search_tool, calc_tool])` -> ["search_tool", "calc_tool"].
    Same shape as _extract_tools_bound, but bind_tools takes tools as the
    first positional argument, not a tools= keyword."""
    if not call_node.args or not isinstance(call_node.args[0], (ast.List, ast.Tuple)):
        return None
    names = [elt.id for elt in call_node.args[0].elts if isinstance(elt, ast.Name)]
    return names or None


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


def _find_starred_tools_value(call_node, assigns_by_func_and_name, func_ctx):
    """`.create(**api_kwargs)` where `api_kwargs` was built (in this same
    function) as a Dict literal containing a "tools" key -- the dominant
    real shape SDK-wrapper code uses instead of a literal `tools=` keyword
    at the call site itself (confirmed real via NousResearch/hermes-agent's
    `api_kwargs = {..., "tools": self.tools}; client.create(**api_kwargs)`).
    One-hop, same-function lookback -- same idea and same limits as the
    taint-source lookback above, not real dataflow propagation."""
    for kw in call_node.keywords:
        if kw.arg is not None or not isinstance(kw.value, ast.Name):
            continue
        source = assigns_by_func_and_name.get((func_ctx, kw.value.id))
        value = _dict_value_for_key(source, "tools")
        if value is not None:
            return value
    return None


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


def _resolve_variable_aliases(tree, *framework_dicts):
    """
    Propagates plain variable-to-variable reassignment (`worker = kb`) so
    `worker` inherits whatever `kb` already resolved to, letting a later
    `worker.add(...)` still attribute correctly instead of being dropped as
    unattributed. Only bare `x = y` counts as an alias -- `x = y.foo()` or
    `x = SomeCall()` are handled separately by the creation-detection logic
    above, not here.

    Accepts any number of variable->framework dicts (store, agent, LLM
    client, ...) and propagates aliases within each independently.

    Fixed-point loop: repeats until nothing new is added, so a multi-hop
    chain (`a = kb`, then later `b = a`) resolves correctly regardless of
    which order the assignments appear in the source. Mutates the dicts
    in place.
    """
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            # Same shape for `x = y` and an annotated `x: T = y`; only the
            # target/value accessors differ.
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    continue
                target_node, value_node = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target_node, value_node = node.target, node.value
            else:
                continue
            if not isinstance(value_node, ast.Name) or not isinstance(target_node, ast.Name):
                continue
            target = target_node.id
            source_var = value_node.id
            for d in framework_dicts:
                if source_var in d and target not in d:
                    d[target] = d[source_var]
                    changed = True


def _frameworks_for_call_identifier(func_node, import_aliases):
    """
    Narrows framework confirmation to ONLY the framework(s) THIS SPECIFIC
    call's identifier can actually be traced back to via an import --
    not "is this framework imported anywhere in the file at all."

    Confirmed as a real, exploitable gap by testing: a file that imports
    Agno for a legitimate agent, but separately defines its own unrelated
    local `class Workflow:`, had `Workflow()` wrongly confirmed as Agno --
    purely because Agno appeared somewhere else in the same file's
    imports, with no check that THIS "Workflow" identifier actually came
    from Agno at all.

    Bare call (`Workflow(...)`): resolve the bare name through
    import_aliases. If it wasn't imported from anywhere (locally defined,
    or a builtin), it returns an empty set -- it can't be confirmed as any
    tracked framework, no matter what else the file imports.

    Qualified call (`agno.Workflow(...)`, or an aliased module,
    `ag.Workflow(...)`): the module is resolved from the qualifying
    prefix's own import, not from the file's imports in general.
    """
    if isinstance(func_node, ast.Name):
        canonical = import_aliases.get(func_node.id)
        if canonical is None:
            return set()
    elif isinstance(func_node, ast.Attribute) and isinstance(func_node.value, ast.Name):
        base = func_node.value.id
        canonical = import_aliases.get(base) or base
    else:
        return set()
    fw = _resolve_module_framework(canonical)
    if fw == "Claude Agent SDK" and _canonical_call_text(func_node, import_aliases) not in {
        f"{package}.{entry}("
        for package in ("claude_agent_sdk", "claude_code_sdk")
        for entry in ("query", "ClaudeSDKClient")
    }:
        return set()  # Require the actual export and a surviving import binding.
    return {fw} if fw else set()


def _is_non_agent_compile_call(func_node, import_aliases):
    """True when `func_node` -- the callee expression of an agent_creation
    candidate call -- contains a `<module>.compile(` on a module in
    _NON_AGENT_COMPILE_MODULES anywhere in it, e.g. `re.compile(...)`.

    Needed because the generic "Graph Compile" agent_creation pattern is a
    bare `\\.compile\\s*\\(` matched against the callee's full unparsed
    text (call_text), meant for LangGraph's `workflow.compile()` but
    textually indistinguishable from any other object's `.compile(`
    method -- including one nested inside a larger expression, e.g.
    `re.compile(...).match(` or `self._app.action(re.compile(...))(`,
    where the OUTER call is `.match(`/`self._app.action(...)(` but the
    substring match still fires because `_match_constructor_text` searches
    the whole call_text, not just the outermost identifier. Walking the
    same func_node subtree here (instead of only its outermost attribute)
    mirrors that substring behavior so the exclusion actually cancels it.

    Confirmed as a real, severe false-positive source by testing against
    NousResearch/hermes-agent: 903 of 909 "agents" detected there were
    plain `re.compile(...)` calls, plus 2 more from the nested shapes
    above. Not specific to that repo -- any Python file that imports `re`
    and calls `re.compile()` (nearly all of them) hits this.
    """
    for node in ast.walk(func_node):
        if not (isinstance(node, ast.Attribute) and node.attr == "compile"):
            continue
        base = node.value
        if not isinstance(base, ast.Name):
            continue
        canonical = import_aliases.get(base.id, base.id)
        if canonical.split(".")[0] in _NON_AGENT_COMPILE_MODULES:
            return True
    return False


LLM_CLIENT_CONSTRUCTORS = {
    "OpenAI": "OpenAI SDK",
    "AsyncOpenAI": "OpenAI SDK",
    "AzureOpenAI": "OpenAI SDK",
    "AsyncAzureOpenAI": "OpenAI SDK",
    "Anthropic": "Anthropic SDK",
    "AsyncAnthropic": "Anthropic SDK",
    # Provider SDKs; Mistral request paths are handled separately below.
    "Groq": "Groq SDK",
    "AsyncGroq": "Groq SDK",
    "Mistral": "Mistral SDK",  # mistralai v1+ client class (was MistralClient pre-1.0)
    "MistralClient": "Mistral SDK",
    "Together": "Together SDK",
    "AsyncTogether": "Together SDK",
}

# Option A: tools are only reliably detectable for a CUSTOM (non-tracked-
# framework) agent at the point they're actually handed to the LLM API --
# every real implementation, however it's built internally, has to funnel
# its tools through the SDK's own `tools=` parameter to reach the model at
# all. This is confirmed the same way as everything else: the receiving
# client must trace back to a real OpenAI/Anthropic SDK import.
LLM_TOOL_CALL_METHODS = {"create", "stream"}  # .chat.completions.create(, .messages.create(, .messages.stream(

# Provider-key evidence for otherwise unrecognized clients.
LLM_ENV_VAR_PROVIDERS = {
    "GROQ_API_KEY": "Groq SDK",
    "TOGETHER_API_KEY": "Together SDK",
    "MISTRAL_API_KEY": "Mistral SDK",
    "COHERE_API_KEY": "Cohere SDK",
    "DEEPSEEK_API_KEY": "DeepSeek SDK",
    "XAI_API_KEY": "xAI SDK",
    "GROK_API_KEY": "xAI SDK",
    "OPENROUTER_API_KEY": "OpenRouter",
    "FIREWORKS_API_KEY": "Fireworks SDK",
    "PERPLEXITY_API_KEY": "Perplexity SDK",
    "REPLICATE_API_TOKEN": "Replicate",
    "HUGGINGFACE_API_KEY": "HuggingFace",
    "HUGGINGFACEHUB_API_TOKEN": "HuggingFace",
    "HF_TOKEN": "HuggingFace",
    "GOOGLE_API_KEY": "Google GenAI",
    "GEMINI_API_KEY": "Google GenAI",
    "CEREBRAS_API_KEY": "Cerebras SDK",
    "NVIDIA_API_KEY": "NVIDIA NIM",
}


def _env_var_provider(node):
    """Recognize the key of an actual environment lookup, not nearby strings."""
    key = None
    if isinstance(node, ast.Call):
        path = _resolve_expr_text(node.func)
        if path in {"os.getenv", "os.environ.get", "environ.get"} and node.args:
            key = node.args[0]
    elif isinstance(node, ast.Subscript):
        if _resolve_expr_text(node.value) in {"os.environ", "environ"}:
            key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return LLM_ENV_VAR_PROVIDERS.get(key.value)
    return None


def _call_references_llm_env_var(call_node):
    for arg in list(call_node.args) + [kw.value for kw in call_node.keywords]:
        for node in ast.walk(arg):
            provider = _env_var_provider(node)
            if provider:
                return provider
    return None


def _llm_constructor_name(func, import_aliases):
    canonical = _canonical_call_text(func, import_aliases)
    return canonical[:-1].rsplit(".", 1)[-1] if canonical else _callee_simple_name(func)


def _build_llm_client_factory_functions(tree, import_aliases):
    """
    Detects `def f(...): ... return OpenAI(...)` (or AsyncOpenAI/Anthropic/
    AsyncAnthropic) defined in THIS file -- single-hop, same idea as
    _build_subclass_extensions -- so `self.client = self._init_client(...)`
    still resolves to the real SDK framework even though the constructor
    call itself lives in a different function's body than the assignment.
    Confirmed necessary by testing against NousResearch/hermes-agent, whose
    client is built through exactly this indirection; without it, the
    client variable never registers, so a later confirmed `tools=` call on
    it goes completely undetected.
    """
    factories = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for stmt in ast.walk(fn):
            if not (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call)):
                continue
            call = stmt.value
            name = _llm_constructor_name(call.func, import_aliases)
            if name not in LLM_CLIENT_CONSTRUCTORS:
                continue
            call_frameworks = _frameworks_for_call_identifier(call.func, import_aliases)
            if LLM_CLIENT_CONSTRUCTORS[name] in call_frameworks:
                factories[fn.name] = LLM_CLIENT_CONSTRUCTORS[name]
                break
    return factories


def _build_llm_client_wrapper_classes(tree, import_aliases):
    """Map local wrapper classes whose ``__init__`` creates an SDK client.

    This is deliberately limited to one hop and to the constructor body: a
    local class is only treated as an LLM client when its own initialization
    contains a confirmed SDK constructor.  That lets callers use a wrapper
    object through an SDK-shaped interface without promoting arbitrary
    ``*Client``/``*Agent`` classes to LLM clients.
    """
    wrappers = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        init = next(
            (node for node in cls.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name == "__init__"),
            None,
        )
        if init is None:
            continue
        for node in ast.walk(init):
            if not isinstance(node, ast.Call):
                continue
            name = _llm_constructor_name(node.func, import_aliases)
            framework = LLM_CLIENT_CONSTRUCTORS.get(name)
            if framework is None:
                continue
            if framework in _frameworks_for_call_identifier(node.func, import_aliases):
                wrappers[cls.name] = framework
                break
    return wrappers


def _build_llm_env_var_classes(tree):
    """Map local classes with provider-key lookups to candidate providers."""
    providers = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for node in ast.walk(cls):
            provider = _env_var_provider(node)
            if provider:
                providers[cls.name] = provider
                break
    return providers


def _extract_literal_tool_names(tools_node):
    """
    Best-effort: if `tools=` is a literal list/tuple, pull out every dict's
    'name' key value found anywhere within it -- handles both OpenAI's
    nested {"function": {"name": ...}} and Anthropic's flat {"name": ...}
    schemas with the same simple walk, since it just looks for a "name"
    key at any depth rather than assuming one specific shape.

    Returns None (not an empty list) when tools_node isn't a literal list
    at all -- e.g. `tools=get_tool_definitions()` (a real, confirmed
    Hermes-agent pattern). None means "tool-calling is confirmed, but the
    specific names can't be extracted without evaluating a function call,
    which this project deliberately doesn't do" -- distinguishing "found
    nothing" from "found something but couldn't see inside it" instead of
    conflating the two into a bare empty list.
    """
    if not isinstance(tools_node, (ast.List, ast.Tuple)):
        return None
    names = []
    for elt in tools_node.elts:
        for node in ast.walk(elt):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "name"
                            and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                        names.append(v.value)
    return names


# Marker patterns: plain line-based regex, NOT AST-verified like the rest
# of this file. These catch custom, repo-specific conventions that have no
# framework import to confirm them against -- so they're reported as
# located observations (file + line, framework: null), never counted into
# any confirmed metric.
_TOOL_USE_MARKER_PATTERN = re.compile(
    r"tool_registry|ToolRegistry\(|register_tool\(|get_tool_definitions\(|"
    r"handle_function_call\(|discover_builtin_tools\("
)

# Agent markers: a repo building its own agent abstraction rather than
# using a tracked framework's constructor. Word-boundary gated and
# case-insensitive on "agent" so it catches AIAgent/BaseAgent/agent_loop/
# run_agent etc. without firing on unrelated substrings ("management",
# "agenda"). Deliberately broad -- this is the "something agent-shaped is
# here" signal for repos like NousResearch/hermes-agent whose entire
# agent implementation is hand-rolled and therefore invisible to
# framework-based detection.
_AGENT_MARKER_PATTERN = re.compile(
    r"\bclass\s+\w*Agent\w*\b"           # class AIAgent, class BaseAgent
    r"|\bdef\s+\w*agent\w*\s*\("          # def run_agent(, def build_agent(
    r"|(?<![\w.])agents?\s*="             # agent = ..., agents = ... (bare only)
    r"|\bself\.agents?\s*="               # self.agent = ...
    r"|\bagent_loop\b|\brun_conversation\s*\(",
    re.IGNORECASE,
)
# NB the `(?<![\w.])` guard on the assignment case: without it, this fired
# on `user_agent = "Mozilla/5.0"` (an HTTP header, nothing to do with AI
# agents) -- confirmed by testing. The paper's own Section III-B makes the
# same point: "agent" appears in many unrelated contexts (user agents,
# build agents), which is exactly why framework-based detection is the
# primary signal and these markers are only a secondary, unconfirmed one.

_TOOL_DEFINITION_MARKER_PATTERN = re.compile(
    r"@tool\b|@function_tool\b|@agent\.tool\b|@agent\.tool_plain\b|"
    r"@mcp\.tool\b|@server\.call_tool\b|@server\.list_tools\b|"
    r"@controller\.action\b|"
    r"\bBaseTool\b|\bFunctionTool\s*\(|\bQueryEngineTool\s*\(|"
    r"\bStructuredTool\.from_function\s*\(|"
    r"\bnew\s+DynamicStructuredTool\s*\(|\bnew\s+DynamicTool\s*\(|"
    r"\bcreateTool\s*\(|\buseCopilotAction\s*\(|"
    r"\bregisterTool\s*\(|\bserver\.tool\s*\(|"
    r"\bserver\.setRequestHandler\s*\(\s*ListToolsRequestSchema|"
    r"\bserver\.setRequestHandler\s*\(\s*CallToolRequestSchema"
)



def _scan_line_markers(source_lines, pattern):
    """Plain per-line regex scan -> [{line, matched}]. One hit per line
    (the first match), so a single line can't flood the output."""
    hits = []
    for idx, text in enumerate(source_lines, start=1):
        m = pattern.search(text)
        if m:
            hits.append({"line": idx, "matched": m.group(0).strip()})
    return hits


# A small lexical pass keeps comments/strings out of creation counts and lets
# tool extraction balance nested object literals without a JS parser dependency.
_JS_TOKEN_RE = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|"
    r"`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|\.\.\.|\+\+|--|[^\s]", re.DOTALL,
)
_JS_REGEX_RE = re.compile(r"/(?:\\[^\r\n]|\[(?:\\[^\r\n]|[^\]\\\r\n])*\]|[^/\\\[\r\n])+/[a-z]*")
_JS_EXPRESSION_PREFIXES = {
    "(", "[", "{", ",", ";", ":", "=", "!", "?", "&", "|", "+", "-", "*", "/", "%",
    "~", "^", "<", ">", "return", "throw", "case", "yield", "await", "void", "typeof",
    "delete", "in", "instanceof", "else", "do",
}
_JS_GRAPH_COMPILE_CONTEXT_RE = re.compile(r"StateGraph\s*\(|\.addNode\s*\(")


def _js_tokens(source):
    matches, parens, braces = [], [], []
    position, regex_allowed = 0, True
    while (match := _JS_TOKEN_RE.search(source, position)) is not None:
        token = match.group()
        position = match.end()
        if token.startswith(("//", "/*")):
            continue
        # A slash after an operand is division; in an expression-start position
        # a complete regex is one opaque token, including escapes/character classes.
        if token == "/" and regex_allowed:
            literal = _JS_REGEX_RE.match(source, match.start())
            if literal:
                match, token, position = literal, literal.group(), literal.end()
        previous = matches[-1].group() if matches else ""
        if token == "(":
            parens.append(previous in {"if", "for", "while", "with", "switch", "catch"}
                          or (previous == "await" and len(matches) > 1
                              and matches[-2].group() == "for"))
        if token == "{":
            braces.append(previous in {"", ";", "{", "}", ")", "else", "do", "try", "finally"})
        if token == ")":
            regex_allowed = parens.pop() if parens else False
        elif token == "}":
            regex_allowed = braces.pop() if braces else False
        else:
            regex_allowed = token in _JS_EXPRESSION_PREFIXES and previous != "."
        matches.append(match)
    return matches


def _js_parts(tokens):
    """Split comma-separated expressions, keeping nested expressions intact."""
    start, depth = 0, 0
    for i, token in enumerate(tokens):
        if token in ("(", "[", "{"):
            depth += 1
        elif token in (")", "]", "}"):
            depth -= 1
        elif token == "," and depth == 0:
            yield tokens[start:i]
            start = i + 1
    yield tokens[start:]


def _js_group(tokens, start):
    depth = 0
    for i in range(start, len(tokens)):
        if tokens[i] in ("(", "[", "{"):
            depth += 1
        elif tokens[i] in (")", "]", "}"):
            depth -= 1
            if depth == 0:
                return tokens[start + 1:i]
    return []


def _js_property(tokens, name):
    for part in _js_parts(tokens):
        if len(part) > 2 and part[0].strip("\"'") == name and part[1] == ":":
            return part[2:]
    return []


def _js_tools(expression, tokens, filename, sources, seen=frozenset()):
    """Resolve explicit arrays, local bindings and one-hop imported factories.

    This counts statically supplied tools across modes, not framework defaults
    or the tools active in one particular runtime invocation.
    """
    if not expression:
        return []
    if expression[0] == "[":
        return [name for part in _js_parts(_js_group(expression, 0))
                for name in _js_tools(part, tokens, filename, sources, seen)]
    if expression[0] == "{":
        names = []
        for part in _js_parts(_js_group(expression, 0)):
            if not part:
                continue
            value = part[2:] if len(part) > 2 and part[1] == ":" else part
            if len(value) == 1 and re.fullmatch(r"[A-Za-z_$][\w$]*", value[0]):
                names.append(value[0])
            else:
                names.extend(_js_tools(value, tokens, filename, sources, seen))
        return names
    if expression[0] == "...":
        return _js_tools(expression[1:], tokens, filename, sources, seen)
    if expression[:1] == ["new"] and len(expression) > 3 and expression[3] == "{":
        name = _js_property(_js_group(expression, 3), "name")
        return [name[0][1:-1]] if name and name[0].startswith(("'", '\"')) else []
    name = expression[0]
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return []
    key = (filename, name)
    if key in seen:
        return []
    seen = seen | {key}
    is_call = len(expression) > 1 and expression[1] == "("
    for i, token in enumerate(tokens):
        if token in ("const", "let", "var") and tokens[i + 1:i + 3] == [name, "="]:
            value = next(_js_parts(tokens[i + 3:]))
            return _js_tools(value, tokens, filename, sources, seen)
        if is_call and token == "function" and tokens[i + 1:i + 3] == [name, "("]:
            params = _js_group(tokens, i + 2)
            body_start = i + 4 + len(params)
            # Skip a simple TypeScript return annotation (e.g. Tool[]).
            while body_start < len(tokens) and tokens[body_start] != "{":
                body_start += 1
            body = _js_group(tokens, body_start)
            tools, depth = [], 0
            for j, value in enumerate(body):
                if value == "return" and depth == 0 and body[j + 1:j + 2] == ["["]:
                    tools.extend(_js_tools(body[j + 1:], body, filename, sources, seen))
                if value in ("(", "[", "{"):
                    depth += 1
                elif value in (")", "]", "}"):
                    depth -= 1
            return tools
        if token == "import" and tokens[i + 1:i + 2] == ["{"]:
            imports = _js_group(tokens, i + 1)
            tail = tokens[i + len(imports) + 3:i + len(imports) + 5]
            if len(tail) != 2 or tail[0] != "from" or not sources:
                continue
            for part in _js_parts(imports):
                if not part or part[-1] != name:
                    continue
                module = tail[1][1:-1]
                if not module.startswith("."):
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(filename), module))
                stem = posixpath.splitext(target)[0]
                for path in (target, stem + ".ts", stem + ".tsx", target + ".ts", target + "/index.ts"):
                    if path in sources:
                        imported = [m.group() for m in _js_tokens(sources[path])]
                        return _js_tools([part[0], *expression[1:]], imported, path, {}, seen)
    # Match Python's explicit-list behavior for unresolved tool variables.
    return [] if is_call else [name]


# Confirmed JS/TS tool-DEFINITION detection, independent of agent detection.
#
# Covers two shapes neither `_js_tools` above (which resolves tools BOUND to
# an already-detected agent) nor the plain-text `_TOOL_DEFINITION_MARKER_PATTERN`
# markers (unconfirmed, never counted) can see:
#   1. A plain-object MCP tool registry (`{name, description, inputSchema}`
#      objects assembled into an array via imports/spreads, e.g. Mastra-less
#      hand-rolled MCP servers) that is actually served through an MCP
#      tools/list or dispatch handler, following the catalog's imports --
#      object shape alone is NOT enough; the handler must reference it.
#   2. A tool object passed to WebMCP's `provider.registerTool(tool, ...)`,
#      whose `name` may be a string literal or a member expression referring
#      to an imported name constant (`WEBMCP_SPA_TOOL.openCountryBrief`).
_TOOL_SCHEMA_KEYS = {"inputSchema", "parameters", "schema"}
_TOOL_SHAPE_KEYS = {"description", "execute"} | _TOOL_SCHEMA_KEYS
_MCP_DISPATCH_MARKER_RE = re.compile(
    r"['\"]tools/list['\"]|\b(?:ListToolsRequestSchema|CallToolRequestSchema)\b"
)
# Naming convention for an assembled MCP tool CATALOG specifically --
# TOOL_REGISTRY, TOOLS_REGISTRY, a bare TOOLS, or anything ending in
# `_REGISTRY`. Deliberately does NOT match `CACHE_TOOLS`/`RPC_TOOLS`-style
# per-category arrays (a bare `*_TOOLS` suffix): those are reached anyway,
# via this same identifier's spread resolution once the enclosing registry
# is found, and independently treating each of THEM as its own entry point
# double-counts every tool once per sub-array's own file, confirmed against
# WorldMonitor's actual layout (`rpc-tools.ts`/`nlp-tools.ts` each mention
# "tools/list" in their own doc comments, which would otherwise make them
# false standalone entry points).
_JS_TOOL_REGISTRY_NAME_RE = re.compile(r"\b(?:[A-Z][A-Z0-9]*_)*REGISTRY\b|\bTOOLS\b")


def _js_tool_def_object_name(obj_tokens, tokens, filename, sources, seen):
    """Return a tool-shaped object's statically resolved string name."""
    keys, name_val = set(), None
    for part in _js_parts(obj_tokens):
        if len(part) > 2 and part[1] == ":":
            key = part[0].strip("\"'")
            keys.add(key)
            if key == "name":
                name_val = part[2:]
        elif len(part) == 1:
            keys.add(part[0])
    if "name" not in keys or not (keys & _TOOL_SHAPE_KEYS) or not name_val:
        return None
    if len(name_val) == 1 and name_val[0][:1] in ("'", '"'):
        return name_val[0][1:-1]
    names = _js_tool_registry_names(name_val, tokens, filename, sources, seen, literal_name=True)
    return names[0] if names else None



def _js_skip_type_annotation(tokens, i):
    """`i` points just past a declared variable's name. If a TS type
    annotation follows (`: ToolDef[]`), skip past it and return the index of
    the `=` that follows; otherwise `i` already points at (or past) `=`."""
    if tokens[i:i + 1] != [":"]:
        return i
    depth = 0
    j = i + 1
    while j < len(tokens):
        t = tokens[j]
        if t in ("<", "(", "[", "{"):
            depth += 1
        elif t in (">", ")", "]", "}"):
            depth -= 1
        elif t == "=" and depth <= 0:
            return j
        j += 1
    return j


def _js_tool_registry_names(expression, tokens, filename, sources, seen, literal_name=False):
    """
    Resolve an assembled tool-registry array to its members' literal names:
    array literals, `...spread` of another array (same-file or imported),
    and object-literal tool definitions. Recurses across import hops (unlike
    `_js_tools`, which only follows one hop) because a real registry is
    typically assembled in one file from arrays defined in others, e.g.
    `TOOL_REGISTRY = [...CACHE_TOOLS, ...RPC_TOOLS]` where each of those is
    itself imported from its own module.
    """
    if not expression:
        return []
    if literal_name and expression[0][:1] in ("'", '"'):
        return [expression[0][1:-1]] if len(expression) == 1 or expression[1] in (";", "as") else []
    if literal_name and expression[0] == "{":
        return []
    if expression[0] == "[":
        return [name for part in _js_parts(_js_group(expression, 0))
                for name in _js_tool_registry_names(part, tokens, filename, sources, seen)]
    if expression[0] == "{":
        name = _js_tool_def_object_name(_js_group(expression, 0), tokens, filename, sources, seen)
        return [name] if name else []
    if expression[0] == "...":
        return _js_tool_registry_names(expression[1:], tokens, filename, sources, seen)
    name = expression[0]
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return []
    key = (filename, name)
    if key in seen:
        return []
    seen = seen | {key}
    for i, token in enumerate(tokens):
        if token in ("const", "let", "var") and tokens[i + 1:i + 2] == [name]:
            eq_idx = _js_skip_type_annotation(tokens, i + 2)
            if tokens[eq_idx:eq_idx + 1] == ["="]:
                value = next(_js_parts(tokens[eq_idx + 1:]))
                if literal_name and value[:4] == ["Object", ".", "freeze", "("]:
                    value = _js_group(value, 3)
                if literal_name and len(expression) > 1:
                    if expression[1:2] != ["."] or len(expression) != 3 or value[:1] != ["{"]:
                        return []
                    value = _js_property(_js_group(value, 0), expression[2])
                return _js_tool_registry_names(value, tokens, filename, sources, seen, literal_name)
        if token == "import" and tokens[i + 1:i + 2] == ["{"]:
            imports = _js_group(tokens, i + 1)
            tail = tokens[i + len(imports) + 3:i + len(imports) + 5]
            if len(tail) != 2 or tail[0] != "from" or not sources:
                continue
            for part in _js_parts(imports):
                if not part or part[-1] != name:
                    continue
                module = tail[1][1:-1]
                if not module.startswith("."):
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(filename), module))
                stem = posixpath.splitext(target)[0]
                for path in (target, stem + ".ts", stem + ".tsx", target + ".ts", target + "/index.ts"):
                    if path in sources:
                        imported = [m.group() for m in _js_tokens(sources[path])]
                        # Unlike `_js_tools`, keep passing `sources` (not `{}`)
                        # so resolution can continue for further import hops
                        # -- a registry assembled from several imported arrays
                        # needs to follow each of THEM to their own files too.
                        return _js_tool_registry_names([part[0], *expression[1:]], imported, path, sources, seen, literal_name)
    # Unresolved: unlike `_js_tools`'s tool-binding fallback, an unresolved
    # registry reference is NOT itself a tool name -- return nothing rather
    # than guess.
    return []


_WEBMCP_REGISTER_TOOL_RE = re.compile(r"\bregisterTool\s*\(")


def _js_registration_names(tokens, filename, sources):
    """Trace registration arguments through local bindings, named wrappers,
    map/forEach callbacks, for-of loops and factory returns (at most 12 hops).

    Token positions and enclosing braces keep same-named local bindings apart.
    Unsupported/computed expressions and cycles produce no confirmed names.
    """
    pairs, scopes, stack = {}, [], []
    for i, token in enumerate(tokens):
        scopes.append(tuple(j for j in stack if tokens[j] == "{"))
        if token in ("(", "[", "{"):
            stack.append(i)
        elif token in (")", "]", "}") and stack:
            start = stack.pop()
            pairs[start] = i

    def expression_at(start):
        end = start
        while end < len(tokens) and tokens[end] not in (";", ",", ")", "]", "}"):
            end = pairs.get(end, end) + 1
        return tokens[start:end]

    # Type parameters can contain commas (e.g. Pick<Provider, 'registerTool'>).
    def parameters(start, end):
        names, depth, first = [], 0, True
        for token in tokens[start:end]:
            if first:
                names.append(token)
                first = False
            if token in ("<", "(", "[", "{"):
                depth += 1
            elif token in (">", ")", "]", "}"):
                depth -= 1
            elif token == "," and depth == 0:
                first = True
        return names

    functions, declarations, iterations = [], [], []
    for i, token in enumerate(tokens):
        if token == "function" and tokens[i + 2:i + 3] == ["("] and i + 2 in pairs:
            close = pairs[i + 2]
            body = close + 1
            while body < len(tokens) and tokens[body] not in ("{", ";", "="):
                body += 1
            if body in pairs and tokens[body] == "{":
                functions.append((tokens[i + 1], i + 1, body, pairs[body],
                                  parameters(i + 3, close)))
        if token in ("const", "let", "var") and i + 1 < len(tokens):
            eq = _js_skip_type_annotation(tokens, i + 2)
            if tokens[eq:eq + 1] == ["="]:
                declarations.append((tokens[i + 1], i, eq + 1))
        if (token in ("map", "forEach") and tokens[max(0, i - 1):i] == ["."]
                and i >= 2 and re.fullmatch(r"[A-Za-z_$][\w$]*", tokens[i - 2])
                and tokens[max(0, i - 3):i - 2] != ["."] and i + 1 in pairs):
            # Bounded callback shape: collection.map((item) => ...).
            arg = i + 2
            if tokens[arg:arg + 1] == ["("] and arg in pairs:
                arrow = pairs[arg] + 1
                params = parameters(arg + 1, pairs[arg])
                if params and tokens[arrow:arrow + 2] == ["=", ">"]:
                    iterations.append((params[0], arrow + 2, pairs[i + 1], [tokens[i - 2]], i - 2))
        if token == "for" and tokens[i + 1:i + 3] == ["(", "const"] and i + 1 in pairs:
            close = pairs[i + 1]
            if tokens[i + 4:i + 5] == ["of"] and tokens[close + 1:close + 2] == ["{"]:
                iterations.append((tokens[i + 3], close + 1, pairs.get(close + 1, close + 1),
                                   tokens[i + 5:close], i))

    def visible(binding, use):
        return scopes[use][:len(scopes[binding])] == scopes[binding]

    def trace(expr, pos, seen=frozenset(), depth=0):
        key = (tuple(expr), pos)
        if not expr or depth >= 12 or key in seen:
            return []
        seen = seen | {key}

        def follow(value, where):
            return trace(value, where, seen, depth + 1)

        if expr[0] in ("[", "{"):
            # Restrict object/array resolution to declarations visible here.
            local_tokens = []
            for _, decl, value in declarations:
                if decl < pos and visible(decl, pos):
                    local_tokens.extend(tokens[decl:value] + expression_at(value) + [";"])
            # Retain imports for statically resolved name constants.
            for i, token in enumerate(tokens):
                if token == "import":
                    local_tokens.extend(expression_at(i) + [";"])
            return _js_tool_registry_names(expr, local_tokens, filename, sources, frozenset())
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", expr[0]):
            return []
        name = expr[0]
        if len(expr) > 1:
            if expr[1] != "(":
                return []
            candidates = [f for f in functions if f[0] == name and visible(f[1], pos)]
            if len(candidates) != 1:
                return []
            _, _, body, end, _ = candidates[0]
            # Only direct returns from the factory body, not nested callbacks.
            return [n for j in range(body + 1, end)
                    if tokens[j] == "return" and scopes[j] == scopes[body] + (body,)
                    for n in follow(expression_at(j + 1), j)]
        for param, start, end, collection, where in reversed(iterations):
            if param == name and start <= pos < end:
                return follow(collection, where)
        owners = [f for f in functions if f[2] < pos < f[3]]
        owner = max(owners, key=lambda f: f[2]) if owners else None
        candidates = [(decl, value) for var, decl, value in declarations
                      if var == name and decl < pos and visible(decl, pos)
                      and (owner is None or name not in owner[4] or decl > owner[2])]
        if candidates:
            decl, value = max(candidates, key=lambda d: (len(scopes[d[0]]), d[0]))
            return follow(expression_at(value), value)
        if owner and name in owner[4]:
            param_index = owner[4].index(name)
            names = []
            for j, token in enumerate(tokens):
                if (token != owner[0] or j == owner[1] or tokens[j + 1:j + 2] != ["("]
                        or tokens[max(0, j - 1):j] == ["."] or not visible(owner[1], j)):
                    continue
                args = list(_js_parts(_js_group(tokens, j + 1)))
                if param_index < len(args):
                    names.extend(follow(args[param_index], j))
            return names
        return []

    for i, token in enumerate(tokens):
        if (token == "registerTool" and tokens[max(0, i - 1):i] == ["."]
                and tokens[i + 1:i + 2] == ["("]):
            arg = next(_js_parts(_js_group(tokens, i + 1)), [])
            yield i, trace(arg, i)


def _detect_webmcp_tool_definitions(source_lines, filename, sources):
    """Confirm tool objects connected to registration by bounded local flow."""
    source = "\n".join(source_lines)
    if not _WEBMCP_REGISTER_TOOL_RE.search(source):
        return []
    matches = _js_tokens(source)
    tokens = [m.group() for m in matches]
    hits, seen_names = [], set()

    def add(name, pos):
        if name and name not in seen_names:
            seen_names.add(name)
            hits.append({
                # registerTool is shared by WebMCP, Pi, OpenClaw and others;
                # the method name confirms no particular framework.
                "name": name, "framework": None,
                "line": source.count("\n", 0, pos) + 1,
                "matched_call": "registerTool(",
            })

    for i, names in _js_registration_names(tokens, filename, sources or {}):
        for name in names:
            add(name, matches[i].start())

    return hits


def _detect_js_registry_tool_definitions(source_lines, filename, sources):
    """Confirm the catalog value served in an MCP handler, following imports
    and registry projections, or a named registry used in a marked lookup.
    Markers in comments and unused/sibling registries do not establish usage.
    """
    source = "\n".join(source_lines)
    matches = _js_tokens(source)
    tokens = [m.group() for m in matches]
    executable = " ".join(tokens)
    if not _MCP_DISPATCH_MARKER_RE.search(executable):
        return []
    hits, seen_names = [], set()
    for i, m in enumerate(matches):
        ident = m.group()
        # Follow the actual catalog value, including imported projections such
        # as TOOL_LIST_RESPONSE = TOOL_REGISTRY.map(...). No sibling-file marker
        # search: only the value served by the handler establishes this link.
        catalog_value = tokens[max(0, i - 2):i] == ["tools", ":"]
        lookup = (_JS_TOOL_REGISTRY_NAME_RE.fullmatch(ident)
                  and tokens[i + 1:i + 4] == [".", "find", "("])
        if not (catalog_value or lookup):
            continue
        for name in _js_tool_registry_names([ident], tokens, filename, sources or {}, frozenset()):
            if name in seen_names:
                continue
            seen_names.add(name)
            hits.append({
                "name": name, "framework": "MCP SDK",
                "line": source.count("\n", 0, m.start()) + 1,
                "matched_call": ident,
            })
    return hits


# MCP SDK `server.tool("name", ...)`/`server.registerTool("name", ...)`:
# requires an MCP SDK import and a static string name.
_JS_MCP_SDK_MODULE_RE = re.compile(r"^['\"`]@modelcontextprotocol/")
_JS_MCP_SERVER_TOOL_METHODS = {"tool", "registerTool"}


def _js_string_value(token):
    """A string literal token's value, or None if interpolated."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"`":
        if token[0] == "`" and "${" in token:
            return None
        return token[1:-1]
    return None


def _detect_js_mcp_server_tool_definitions(source_lines):
    """Confirm MCP SDK tool registrations with a literal or same-file const name."""
    source = "\n".join(source_lines)
    if "@modelcontextprotocol/" not in source:
        return []
    matches = _js_tokens(source)
    tokens = [m.group() for m in matches]
    if not any(
        _JS_MCP_SDK_MODULE_RE.match(token)
        and (tokens[i - 1:i] in (["from"], ["import"]) or tokens[max(0, i - 2):i] == ["require", "("])
        for i, token in enumerate(tokens)
    ):
        return []
    constants = {}
    for i, token in enumerate(tokens):
        if token == "const" and tokens[i + 2:i + 3] == ["="] and tokens[i + 4:i + 5] in ([";"], [","], []):
            value = _js_string_value(tokens[i + 3]) if i + 3 < len(tokens) else None
            if value is not None:
                constants.setdefault(tokens[i + 1], value)
    hits, seen_names = [], set()
    for i, token in enumerate(tokens):
        if (token not in _JS_MCP_SERVER_TOOL_METHODS or tokens[i - 1:i] != ["."]
                or tokens[i + 1:i + 2] != ["("]):
            continue
        first = next(_js_parts(_js_group(tokens, i + 1)), [])
        if len(first) != 1:
            continue
        name = _js_string_value(first[0])
        if name is None:
            name = constants.get(first[0])
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        hits.append({
            "name": name, "framework": "MCP SDK",
            "line": source.count("\n", 0, matches[i].start()) + 1,
            "matched_call": f"{token}(",
        })
    return hits


# HTTP-agent option hints for otherwise unresolved constructors.
# Known HTTP imports are excluded independently of their options.
_UNDICI_AGENT_OPTION_KEYS = {
    "headersTimeout", "bodyTimeout", "connect", "pipelining",
    "keepAliveTimeout", "keepAliveMaxTimeout", "connections", "factory",
    "maxHeaderSize", "maxResponseSize",
}


# These ordinary function names need callee-level import confirmation.
_JS_PACKAGE_REQUIRED = {
    "Claude Agent SDK": ("@anthropic-ai/claude-agent-sdk", "@anthropic-ai/claude-code"),
    "Pi": ("@earendil-works/pi-coding-agent", "@mariozechner/pi-coding-agent",
           "@earendil-works/pi-agent-core", "@mariozechner/pi-agent-core"),
}
_JS_AGENT_EXPORTS = {
    "Claude Agent SDK": {"query"},
    "Pi": {"createAgentSession", "createAgentSessionFromServices", "createAgentSessionRuntime",
           "agentLoop", "agentLoopContinue", "runAgentLoop", "runAgentLoopContinue"},
}

# A parenthesized head belonging to one of these is control flow, not a
# function parameter list -- `for await (const m of query({...})) {` reads
# exactly like `(params) {` to a token scanner, and treating it as one marked
# every binding used inside it as shadowed. That silently zeroed the Agent
# SDK's own streaming idiom, which is how most of its call sites are written.
_JS_CONTROL_FLOW_HEADS = {"if", "for", "while", "switch", "await"}


def _js_sdk_agent_calls(tokens):
    """Resolve direct SDK calls from named/namespace imports and module loads.

    This deliberately handles only explicit bindings. Reassigned or shadowed
    names are rejected conservatively throughout the file. Module-load bindings
    are visible only in their enclosing brace scopes; this is not a full
    JavaScript control-flow or scope resolver.
    """
    bindings, import_tokens, require_binding_positions = {}, set(), set()
    pairs, scopes, stack = {}, [], []
    for i, token in enumerate(tokens):
        scopes.append(tuple(j for j in stack if tokens[j] == "{"))
        if token in ("(", "[", "{"):
            stack.append(i)
        elif token in (")", "]", "}") and stack:
            pairs[stack.pop()] = i

    def binding_names(pattern):
        # Only binding positions count: object keys, types and default-value
        # expressions can refer to the SDK without declaring its name.
        if not pattern:
            return set()
        if pattern[0] == "...":
            return binding_names(pattern[1:])
        if pattern[0] == "{":
            names = set()
            for part in _js_parts(_js_group(pattern, 0)):
                if len(part) > 1 and part[1] == ":":
                    part = part[2:]
                names.update(binding_names(part))
            return names
        if pattern[0] == "[":
            return set().union(*(binding_names(part) for part in
                                 _js_parts(_js_group(pattern, 0))))
        return {pattern[0]} if re.fullmatch(r"[A-Za-z_$][\w$]*", pattern[0]) else set()

    def register(parts, label, position, namespace=False, commonjs=False):
        def add(local, exported):
            bindings.setdefault(local, []).append(
                (label, exported, scopes[position], position))
        if namespace:
            if len(parts) == 1:
                add(parts[0], None)
            return
        for part in _js_parts(parts):
            separator = ":" if commonjs else "as"
            if len(part) == 1:
                exported = local = part[0]
            elif len(part) == 3 and part[1] == separator:
                exported, local = part[0], part[2]
            else:
                continue  # includes type-only imports
            if exported in _JS_AGENT_EXPORTS[label]:
                add(local, exported)

    def framework(spec):
        if not spec.startswith(("'", '"')):
            return None
        return next((label for label, packages in _JS_PACKAGE_REQUIRED.items()
                     if spec[1:-1] in packages), None)

    for i, token in enumerate(tokens):
        if token == "import" and tokens[i + 1:i + 2] == ["{"]:
            parts = _js_group(tokens, i + 1)
            end = i + len(parts) + 3
            if tokens[end:end + 1] == ["from"] and end + 1 < len(tokens):
                label = framework(tokens[end + 1])
                if label:
                    register(parts, label, i)
                    import_tokens.update(range(i, end + 2))
        elif (token == "import" and tokens[i + 1:i + 3] == ["*", "as"]
              and tokens[i + 4:i + 5] == ["from"] and i + 5 < len(tokens)):
            label = framework(tokens[i + 5])
            if label:
                register([tokens[i + 3]], label, i, namespace=True)
                import_tokens.update(range(i, i + 6))
        elif token in {"const", "let", "var"}:
            end = i + 2
            parts = tokens[i + 1:i + 2]
            destructured = parts == ["{"]
            if destructured:
                parts = _js_group(tokens, i + 1)
                end = i + len(parts) + 3
            if tokens[end:end + 1] != ["="]:
                continue
            load = end + 1
            awaited = tokens[load:load + 1] == ["await"]
            if awaited:
                load += 1
            if (tokens[load:load + 1] not in (["require"], ["import"])
                    or tokens[load + 1:load + 2] != ["("]
                    or tokens[load + 3:load + 4] != [")"]):
                continue
            if tokens[load] == "import" and not awaited:
                continue  # import() yields a Promise, not the SDK namespace
            if tokens[load + 4:load + 5] in (["."], ["["]):
                continue  # a projected value is not the module namespace
            label = framework(tokens[load + 2])
            if label:
                register(parts, label, i, namespace=not destructured, commonjs=True)
                import_tokens.update(range(i, load + 4))
                if tokens[load] == "require":
                    require_binding_positions.add(i)

    invalid = set()
    declaration_names = set()
    for i, token in enumerate(tokens):
        if i in import_tokens:
            continue
        if token in {"const", "let", "var"}:
            # Walk all declarators, jumping over initializer expressions.
            start = i + 1
            while start < len(tokens):
                end = pairs.get(start, start) + 1
                declaration_names.update(binding_names(tokens[start:end]))
                j = end
                while j < len(tokens) and tokens[j] not in {";", ",", ")", "}", "of", "in"}:
                    j = pairs.get(j, j) + 1
                if tokens[j:j + 1] != [","]:
                    break
                start = j + 1
        if token in bindings and tokens[i - 1:i] != ["."]:
            # Mutating a namespace export also removes its SDK identity.
            if (tokens[i + 1:i + 2] == ["."]
                    and tokens[i + 3:i + 4] == ["="]
                    and tokens[i + 4:i + 5] not in (["="], [">"])
                    and any(exported is None and tokens[i + 2] in _JS_AGENT_EXPORTS[label]
                            for label, exported, _, _ in bindings[token])):
                invalid.add(token)
            if (tokens[i + 1:i + 2] == ["["]
                    and tokens[i + 3:i + 5] == ["]", "="]
                    and tokens[i + 4:i + 6] != ["=", "="]
                    and any(exported is None and tokens[i + 2].strip("\"'") in _JS_AGENT_EXPORTS[label]
                            for label, exported, _, _ in bindings[token])):
                invalid.add(token)
            if (tokens[i - 1:i] in (["function"], ["class"])
                    or (tokens[i + 1:i + 2] == ["="]
                        and tokens[i + 2:i + 3] != ["="])):
                invalid.add(token)
        if token == "(" and tokens[i - 1:i] not in ([kw] for kw in _JS_CONTROL_FLOW_HEADS):
            end = pairs.get(i, i) + 1
            parameter_end = end - 1
            # TS functions/arrows may have a return annotation before their
            # body. Do not mistake a typed shadow parameter for an SDK import.
            if tokens[end:end + 1] == [":"]:
                end += 1
                angle = 0
                while end < len(tokens):
                    if angle == 0 and (tokens[end] in {"{", ";", "}"}
                                       or tokens[end:end + 2] == ["=", ">"]):
                        break
                    angle += (tokens[end] == "<") - (tokens[end] == ">")
                    end = pairs.get(end, end) + 1
            if tokens[end:end + 1] == ["{"] or tokens[end:end + 2] == ["=", ">"]:
                for part in _js_parts(tokens[i + 1:parameter_end]):
                    declaration_names.update(binding_names(part))
    invalid.update(declaration_names.intersection(bindings))
    # require() can itself be a parameter/local function. A call to that
    # replacement is not evidence that Node loaded the named SDK package.
    if "require" in declaration_names or any(
            token == "require" and (tokens[i - 1:i] == ["function"]
                                    or tokens[i + 1:i + 2] == ["="])
            for i, token in enumerate(tokens)):
        for local, entries in bindings.items():
            if any(position in require_binding_positions
                   for _, _, _, position in entries):
                invalid.add(local)

    calls = {}
    for i, token in enumerate(tokens):
        if token not in bindings or token in invalid or i in import_tokens:
            continue
        if tokens[max(0, i - 1):i] in (["."], ["function"], ["new"]):
            continue
        visible = [binding for binding in bindings[token]
                   if scopes[i][:len(binding[2])] == binding[2]
                   and (tokens[binding[3]] == "import" or binding[3] < i)]
        if not visible:
            continue
        label, exported, _, _ = max(visible, key=lambda binding: (len(binding[2]), binding[3]))
        opening = i + 1
        if exported is None:
            if (tokens[i + 1:i + 2] != ["."] or i + 2 >= len(tokens)
                    or tokens[i + 2] not in _JS_AGENT_EXPORTS[label]):
                continue
            opening = i + 3
        if tokens[opening:opening + 1] == ["("]:
            end = opening + len(_js_group(tokens, opening)) + 2
            # Typed methods put a return annotation between ')' and '{'.
            # Require a declaration prefix so ternaries/case labels containing
            # actual calls (e.g. condition ? query(...) : other) still count.
            typed_method = (tokens[end:end + 1] == [":"] and exported is not None
                            and i > 0 and tokens[i - 1] in {
                                "{", "}", ";", ",", "*", "async", "static",
                                "public", "private", "protected", "override", "abstract",
                            })
            if tokens[end:end + 1] == ["{"] or typed_method:
                continue  # method/function declaration, not a call
            calls[opening] = (i, label)
    return calls


def _detect_js_named_agents(source_lines, agent_creation_category, allowed_langs_by_fw,
                            filename="", javascript_sources=None):
    if agent_creation_category is None:
        return []
    source = "\n".join(source_lines)
    matches = _js_tokens(source)
    tokens = [m.group() for m in matches]
    # Preserve offsets and line numbers while hiding non-code evidence.
    code = list(re.sub(r"[^\n]", " ", source))
    for m in matches:
        if not m.group().startswith(("'", '\"', "`", "/")):
            code[m.start():m.end()] = m.group()
    code = "".join(code)
    has_mastra_agent = False
    has_http_agent = False
    for i, token in enumerate(tokens):
        if token != "import" or tokens[i + 1:i + 2] != ["{"]:
            continue
        imports = _js_group(tokens, i + 1)
        tail = tokens[i + len(imports) + 3:i + len(imports) + 5]
        if (len(tail) == 2 and tail[0] == "from"
                and tail[1][1:-1] in {"undici", "http", "https", "node:http", "node:https"}
                and any(part[-1:] == ["Agent"] for part in _js_parts(imports))):
            has_http_agent = True
        if (len(tail) == 2 and tail[0] == "from"
                and tail[1][1:-1] in ("@mastra/core", "@mastra/core/agent")
                and any(part == ["Agent"] for part in _js_parts(imports))):
            has_mastra_agent = True
    has_graph_context = bool(_JS_GRAPH_COMPILE_CONTEXT_RE.search(code))
    call_tokens = {m.start(): i for i, m in enumerate(matches) if m.group() == "("}
    hits, seen = [], set()
    for opening, (start, label) in _js_sdk_agent_calls(tokens).items():
        if label not in agent_creation_category.frameworks:
            continue
        args = _js_group(tokens, opening)
        options = _js_group(args, 0) if args[:1] == ["{"] else []
        hits.append({
            "name": None, "framework": label,
            "line": source.count("\n", 0, matches[start].start()) + 1,
            "matched_call": source[matches[start].start():matches[opening].end()],
            "tools_bound": _js_tools(_js_property(options, "tools"), tokens,
                                     filename, javascript_sources),
        })
        seen.add(matches[opening].start())
    for pattern_str, label, regex, _is_generic in agent_creation_category._ranked:
        allowed_langs = allowed_langs_by_fw.get(label)
        # new McpServer(/new Server( creates a tool-exposing server, not an LLM agent -- excluded from n_agents
        if label == "MCP SDK" or (allowed_langs is not None and "javascript" not in allowed_langs):
            continue
        if label in _JS_PACKAGE_REQUIRED:
            continue
        if "(" not in pattern_str:
            continue
        bare_new_agent = label == "Mastra" and pattern_str.startswith("new Agent")
        for m in regex.finditer(code):
            opening = code.find("(", m.start(), m.end())
            if opening not in call_tokens or opening in seen:
                continue
            if re.fullmatch(r"\.compile\s*\(", m.group().strip()) and not has_graph_context:
                continue
            seen.add(opening)
            args = _js_group(tokens, call_tokens[opening])
            options = _js_group(args, 0) if args[:1] == ["{"] else []
            hit_label = label
            if bare_new_agent and has_http_agent:
                continue
            if bare_new_agent and not has_mastra_agent:
                # Inspect all arguments: AI configuration can be positional,
                # shorthand, or nested under initialState. String/comment text
                # does not match these exact identifier tokens.
                if any(_js_property(options, key) for key in _UNDICI_AGENT_OPTION_KEYS):
                    continue
                if not set(args).intersection({
                        "model", "modelId", "tools", "systemPrompt", "system",
                        "instructions", "messages", "provider", "providerId", "apiKey"}):
                    continue
                hit_label = "Custom"
            hits.append({
                "name": None, "framework": hit_label,
                "line": source.count("\n", 0, m.start()) + 1,
                "matched_call": m.group().strip(),
                "tools_bound": _js_tools(_js_property(options, "tools"), tokens,
                                         filename, javascript_sources),
            })
    return sorted(hits, key=lambda hit: hit["line"])


def _detect_tool_use_markers(source_lines):
    return _scan_line_markers(source_lines, _TOOL_USE_MARKER_PATTERN)


def _detect_agent_markers(source_lines):
    return _scan_line_markers(source_lines, _AGENT_MARKER_PATTERN)


def _detect_tool_definition_markers(source_lines):
    return _scan_line_markers(source_lines, _TOOL_DEFINITION_MARKER_PATTERN)


# `.bind_tools(`/`.bindTools(` -- LangChain's API for handing tools to a
# model outside its packaged agent constructors.
_BIND_TOOLS_RE = re.compile(r"\b(\w+)\.(?:bind_tools|bindTools)\s*\(")
_JS_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _extract_js_bind_tools_names(text, call_end):
    """Extract identifiers from a same-line literal tool array."""
    m = re.match(r"\s*\[([^\]]*)\]", text[call_end:])
    if not m:
        return None
    names = [tok.strip() for tok in m.group(1).split(",")]
    names = [n for n in names if _JS_IDENTIFIER_RE.fullmatch(n)]
    return names or None


def _detect_bind_tools_calls(source_lines):
    hits = []
    for idx, text in enumerate(source_lines, start=1):
        m = _BIND_TOOLS_RE.search(text)
        if m:
            hits.append({
                "line": idx, "variable": m.group(1), "framework": "LangChain",
                "matched_call": m.group(0).strip(),
                "tool_names": _extract_js_bind_tools_names(text, m.end()),
            })
    return hits


def _canonical_call_text(func_node, import_aliases):
    """
    Rewrites a call's text into its CANONICAL (fully-qualified) form using
    the file's own imports, so a locally-renamed import still matches a
    registry pattern written in qualified form.

    `from chromadb import Client as MyDB` then `MyDB()` produces call text
    "MyDB(", which matches nothing -- the registry lists "chromadb.Client(".
    Resolving MyDB -> chromadb.Client via the import table yields
    "chromadb.Client(", which matches. Confirmed by testing: without this,
    that exact (common) aliasing shape was completely invisible.

    Returns None when there's nothing to rewrite (no matching import, or a
    shape this doesn't handle), so callers can skip the extra match attempt.
    """
    if isinstance(func_node, ast.Name):
        canonical = import_aliases.get(func_node.id)
        return f"{canonical}(" if canonical else None
    if isinstance(func_node, ast.Attribute):
        # `cdb.Client(` where `import chromadb as cdb` -> `chromadb.Client(`
        chain = []
        node = func_node
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        canonical_base = import_aliases.get(node.id)
        if not canonical_base:
            return None
        return canonical_base + "." + ".".join(reversed(chain)) + "("
    return None


def _group_custom_agents(llm_tool_calls):
    """
    Collapses per-CALL llm_tool_calls entries into one entry per distinct
    (client variable) -- a hand-rolled agent that loops and calls
    `.create(tools=...)` many times must count as ONE agent, not one per
    call. Mirrors how named_agents already counts one entry per creation
    site rather than per later `.run()` call.
    """
    agents_by_var = {}
    ordered = []
    for t in llm_tool_calls:
        var = t["variable"]
        entry = agents_by_var.get(var)
        if entry is None:
            entry = {
                "variable": var, "framework": t["framework"], "line": t["line"],
                "matched_call": t.get("matched_call"), "tool_names": set(),
            }
            agents_by_var[var] = entry
            ordered.append(entry)
        if t.get("tool_names"):
            entry["tool_names"].update(t["tool_names"])
    for entry in ordered:
        entry["tool_names"] = sorted(entry["tool_names"]) if entry["tool_names"] else None
    return ordered


def _reduce_python(source, agent_creation_category, rag_creation_category, rag_writes_category, rag_reads_category=None, agent_calls_category=None, tool_definition_category=None, external_exports=None):
    """
    Returns (reduced_text, parse_error, named_agents, named_stores,
    store_agent_links, tainted_writes, write_sites, read_sites, call_sites,
    imports, llm_tool_calls, tool_use_markers, agent_markers,
    confirmed_tool_definitions).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        return source, f"{type(e).__name__}: {e}", [], [], [], [], [], [], [], [], [], [], [], []
    except RecursionError as e:
        return source, f"RecursionError: {e}", [], [], [], [], [], [], [], [], [], [], [], []

    source_lines = source.splitlines()
    import_aliases = _build_import_aliases(tree)
    # The newly supported query() name is especially common. Apply the same
    # conservative file-wide shadow/reassignment policy as the JS SDK pass.
    sdk_aliases = {name for name, canonical in import_aliases.items()
                   if canonical.split(".")[0] in {"claude_agent_sdk", "claude_code_sdk"}}
    for node in ast.walk(tree):
        shadow = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shadow = node.name
        elif isinstance(node, ast.arg):
            shadow = node.arg
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            shadow = node.id
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.value, ast.Name):
                shadow = node.value.id
        if shadow in sdk_aliases:
            import_aliases.pop(shadow, None)
    extra_agent_classes, extra_store_classes = _build_subclass_extensions(
        tree, agent_creation_category, rag_creation_category, import_aliases
    )
    llm_client_factory_functions = _build_llm_client_factory_functions(tree, import_aliases)
    llm_client_wrapper_classes = _build_llm_client_wrapper_classes(tree, import_aliases)
    llm_env_var_classes = _build_llm_env_var_classes(tree)

    lines = []
    pending_agent_calls = {}
    pending_store_calls = {}
    pending_llm_clients = {}
    pending_tool_def_calls = {}
    confirmed_tool_definitions = []
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
            callee = _unwrap_subscript_callee(node.func)
            try:
                func_repr = ast.unparse(callee)
            except Exception:
                continue
            call_text = f"{func_repr}("
            lines.append(call_text)

            # Agent creation. Role is decided by pattern shape (does this
            # call look like an agent-creation call at all, across any
            # framework's patterns?); import preference is now built
            # directly into _match_constructor_text -- it scans every
            # matching pattern and prefers whichever one the file's own
            # imports actually confirm, only falling back to a plain
            # specificity guess when nothing is confirmed. This ordering
            # still matters for one reason: import resolution alone can't
            # tell "VectorStoreIndex" (LlamaIndex, a STORE) apart from
            # "ReActAgent" (LlamaIndex, an AGENT) -- both resolve to the
            # same framework via import, so checking agent_creation_category
            # first (and only proceeding to store-check if nothing matched)
            # is what keeps a store from being wrongly read as an agent.
            # Confirmed by testing.
            #
            # Framework confirmation is now scoped to THIS SPECIFIC call's
            # identifier (via _frameworks_for_call_identifier), not "is the
            # framework imported anywhere in this file." Confirmed as a
            # real gap: a file importing Agno for a legitimate agent, but
            # separately defining its own unrelated local `class Workflow`,
            # previously had that unrelated Workflow() wrongly confirmed as
            # Agno purely because Agno appeared elsewhere in the file.
            call_frameworks = _frameworks_for_call_identifier(callee, import_aliases)
            # Canonical (fully-qualified) form of this call, via the file's
            # own imports -- lets a locally-renamed import still match a
            # registry pattern written in qualified form. See
            # _canonical_call_text.
            canonical_text = _canonical_call_text(callee, import_aliases)
            framework = None
            if agent_creation_category is not None:
                framework = _match_constructor_text(call_text, agent_creation_category, call_frameworks)
                if framework is None and canonical_text:
                    framework = _match_constructor_text(canonical_text, agent_creation_category, call_frameworks)
                if framework is not None and _is_non_agent_compile_call(callee, import_aliases):
                    framework = None
            if framework is None and isinstance(callee, ast.Name):
                framework = extra_agent_classes.get(callee.id)
            if framework is not None:
                pending_agent_calls[id(node)] = {
                    "framework": framework,
                    "name": _extract_name_kwarg(node),
                    "line": node.lineno,
                    "tools_bound": _extract_tools_bound(
                        node, assigns_by_func_and_name, func_ctx, callee_name=_callee_simple_name(callee),
                        import_aliases=import_aliases,
                    ),
                    "call_node": node,
                    "matched_call": call_text,  # e.g. "SolidAssistantAgent(" -- so you can eyeball-confirm the match
                }
                continue  # a call site is either an agent or a store, not both

            # Store creation: same role-first order as above.
            store_framework = None
            if rag_creation_category is not None:
                store_framework = _match_constructor_text(call_text, rag_creation_category, call_frameworks)
                if store_framework is None and canonical_text:
                    store_framework = _match_constructor_text(canonical_text, rag_creation_category, call_frameworks)
            if store_framework is None and isinstance(callee, ast.Name):
                store_framework = extra_store_classes.get(callee.id)
            if store_framework is not None:
                pending_store_calls[id(node)] = {
                    "framework": store_framework,
                    "line": node.lineno,
                    "call_node": node,
                    "matched_call": call_text,
                }

            if tool_definition_category is not None:
                tool_def_framework = _match_constructor_text(call_text, tool_definition_category, call_frameworks)
                if tool_def_framework is None and canonical_text:
                    tool_def_framework = _match_constructor_text(canonical_text, tool_definition_category, call_frameworks)
                if tool_def_framework is not None:
                    pending_tool_def_calls[id(node)] = {
                        "framework": tool_def_framework,
                        "line": node.lineno,
                        "matched_call": call_text,
                    }

            # LLM SDK client creation (Option A foundation): confirms which
            # variable is a real OpenAI/Anthropic client, so a later
            # `.chat.completions.create(tools=...)` on it can be trusted as
            # genuine tool-calling evidence rather than a guess. Same
            # per-identifier import confirmation as everything else here.
            llm_client_name = _llm_constructor_name(callee, import_aliases)
            if llm_client_name in LLM_CLIENT_CONSTRUCTORS and LLM_CLIENT_CONSTRUCTORS[llm_client_name] in call_frameworks:
                pending_llm_clients[id(node)] = {
                    "framework": LLM_CLIENT_CONSTRUCTORS[llm_client_name],
                    "line": node.lineno,
                }
            elif llm_client_name in llm_client_factory_functions:
                pending_llm_clients[id(node)] = {
                    "framework": llm_client_factory_functions[llm_client_name],
                    "line": node.lineno,
                }
            elif llm_client_name in llm_client_wrapper_classes:
                pending_llm_clients[id(node)] = {
                    "framework": llm_client_wrapper_classes[llm_client_name],
                    "line": node.lineno,
                }
            else:
                # Fallback evidence; request paths are checked below.
                env_provider = _call_references_llm_env_var(node)
                if env_provider is None and isinstance(callee, ast.Name):
                    env_provider = llm_env_var_classes.get(callee.id)
                if env_provider is not None:
                    pending_llm_clients[id(node)] = {
                        "framework": f"Custom ({env_provider})",
                        "line": node.lineno,
                    }

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                try:
                    lines.append(f"@{ast.unparse(dec)}")
                except Exception:
                    continue
                if (tool_definition_category is not None
                        and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
                    dec_func = dec.func if isinstance(dec, ast.Call) else dec
                    dec_frameworks = _frameworks_for_call_identifier(dec_func, import_aliases)
                    dec_text = f"@{_resolve_expr_text(dec_func)}"
                    dec_fw = _match_constructor_text(dec_text, tool_definition_category, dec_frameworks)
                    if dec_fw is None:
                        canonical_dec = _canonical_call_text(dec_func, import_aliases)
                        if canonical_dec:
                            dec_fw = _match_constructor_text(
                                f"@{canonical_dec[:-1]}", tool_definition_category, dec_frameworks,
                            )
                    if dec_fw is not None:
                        confirmed_tool_definitions.append({
                            "framework": dec_fw, "name": node.name,
                            "line": getattr(dec, "lineno", node.lineno),
                            "matched_call": dec_text,
                        })
            if tool_definition_category is not None and isinstance(node, ast.ClassDef):
                # `class X(BaseTool): name = "x"` -- base confirmed by import.
                tool_name = next((
                    stmt.value.value for stmt in node.body
                    if isinstance(stmt, (ast.Assign, ast.AnnAssign))
                    and any(isinstance(t, ast.Name) and t.id == "name"
                            for t in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]))
                    and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)
                ), None)
                for base in node.bases if tool_name else []:
                    base_text = _resolve_expr_text(base)
                    base_fw = _match_constructor_text(
                        base_text, tool_definition_category,
                        _frameworks_for_call_identifier(base, import_aliases),
                    )
                    if base_fw is not None:
                        confirmed_tool_definitions.append({
                            "framework": base_fw, "name": tool_name,
                            "line": node.lineno, "matched_call": base_text,
                        })
                        break
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lines.append(node.value)
        elif isinstance(node, ast.Assign):
            var_name = _extract_simple_target_name(node.targets)
            if var_name is not None:
                if isinstance(node.value, ast.Call):
                    assign_target_for_call[id(node.value)] = var_name
                assigns_by_func_and_name[(func_ctx, var_name)] = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            # `x: T = expr` -- same as a plain Assign, just a single
            # `.target` instead of a `.targets` list.
            var_name = _resolve_identity(node.target)
            if var_name is not None:
                if isinstance(node.value, ast.Call):
                    assign_target_for_call[id(node.value)] = var_name
                assigns_by_func_and_name[(func_ctx, var_name)] = node.value

    # Resolve agent/store variable names from their assignment (fallback to
    # the assigned variable name when there's no name=/role=/id= kwarg).
    named_agents = []
    agent_var_by_call_id = {}
    agent_framework_by_var = {}  # populated below, used by call_sites/write/read attribution
    # Preliminary view (before wrap-filtering) so a wrapping call can look
    # up which framework its receiver already resolved to.
    prelim_agent_framework = {
        assign_target_for_call[cid]: e["framework"]
        for cid, e in pending_agent_calls.items() if assign_target_for_call.get(cid)
    }
    for call_id, entry in pending_agent_calls.items():
        var_name = assign_target_for_call.get(call_id)
        if entry["name"] is None:
            entry["name"] = var_name
        agent_var_by_call_id[call_id] = var_name
        wrapped_framework = _wrapping_call_framework(entry["call_node"], var_name, prelim_agent_framework)
        if wrapped_framework is not None:
            if var_name:
                agent_framework_by_var[var_name] = wrapped_framework
            continue  # same agent as the receiver -- don't count a second one
        if var_name:
            agent_framework_by_var[var_name] = entry["framework"]
        named_agents.append({
            "name": entry["name"], "framework": entry["framework"],
            "line": entry["line"], "tools_bound": entry["tools_bound"],
            "matched_call": entry["matched_call"],
            # "variable" is the ASSIGNMENT TARGET, kept separate from "name"
            # (which prefers a name=/role=/id= kwarg for display). Only the
            # variable is importable from another module, so cross-file
            # export keying must use this -- keying on "name" meant
            # `researcher = Crew(role="analyst")` was exported as "analyst"
            # and `from agents import researcher` could never match it.
            # Confirmed by testing.
            "variable": var_name,
        })

    named_stores = []
    store_var_by_call_id = {}
    # variable -> single resolved framework, used to deduplicate write/read
    # counting below (was previously counted independently per framework
    # whenever a shared verb like ".add(" matched, regardless of which
    # actual store the call was on).
    store_framework_by_var = {}
    prelim_store_framework = {
        assign_target_for_call[cid]: e["framework"]
        for cid, e in pending_store_calls.items() if assign_target_for_call.get(cid)
    }
    for call_id, entry in pending_store_calls.items():
        var_name = assign_target_for_call.get(call_id)
        store_var_by_call_id[call_id] = var_name
        wrapped_framework = _wrapping_call_framework(entry["call_node"], var_name, prelim_store_framework)
        if wrapped_framework is not None:
            if var_name:
                store_framework_by_var[var_name] = wrapped_framework
            continue  # same store as the receiver -- don't count a second one
        if var_name:
            store_framework_by_var[var_name] = entry["framework"]
        named_stores.append({
            "variable": var_name, "framework": entry["framework"], "line": entry["line"],
            "matched_call": entry["matched_call"],
        })

    for call_id, entry in pending_tool_def_calls.items():
        confirmed_tool_definitions.append({
            "framework": entry["framework"],
            "name": assign_target_for_call.get(call_id),
            "line": entry["line"],
            "matched_call": entry["matched_call"],
        })

    # CROSS-FILE resolution: a store OR agent created in another file and
    # imported here (`from store import kb`, `from agents import researcher`)
    # now resolves, so a write/read/call on it is counted instead of being
    # invisible. Confirmed necessary by testing, for BOTH kinds: without
    # this, `from store import kb` then `kb.add(...)`, and `from agents
    # import researcher` then `researcher.kickoff()`, each produced no
    # finding at all -- systematically under-counting RAG interactions and
    # agent calls in modular codebases.
    #
    # Still strictly evidence-based: the name must appear in a REAL import
    # statement in THIS file, AND the module it names must have been seen
    # in the first pass actually exporting that variable as a confirmed
    # store/agent. A locally-defined name always wins over an imported one
    # -- the *_framework_by_var tables are populated from this file's own
    # creations first, and setdefault below never overwrites them.
    if external_exports:
        # Names reassigned anywhere in THIS file can't be trusted to still
        # hold the imported object -- `from store import kb` followed by
        # `kb = SomethingElse()` means the later kb.add(...) is NOT the
        # imported store. Confirmed by testing: without this guard, that
        # exact shape was wrongly attributed to Chroma.
        locally_reassigned = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    ident = _resolve_identity(t)
                    if ident:
                        locally_reassigned.add(ident)
            elif isinstance(node, ast.AnnAssign):
                ident = _resolve_identity(node.target)
                if ident:
                    locally_reassigned.add(ident)

        for kind, table in (("stores", store_framework_by_var), ("agents", agent_framework_by_var)):
            exports_for_kind = external_exports.get(kind) or {}
            if not exports_for_kind:
                continue
            for local_name, canonical in import_aliases.items():
                if local_name in table:
                    continue  # this file defines it itself -- local always wins
                if local_name in locally_reassigned:
                    continue  # rebound locally after import -- no longer provably the same object
                if "." not in canonical:
                    continue
                module_path, _, imported_name = canonical.rpartition(".")
                framework = exports_for_kind.get(module_path, {}).get(imported_name)
                if framework:
                    table.setdefault(local_name, framework)

    # Rebuild known_store_vars AFTER cross-file resolution, so a store
    # imported from another file also counts as "a known store" when
    # checking `Agent(memory=kb)`-style links. Without this, an agent
    # linked to an imported store formed no link at all, leaving
    # rag_writers/rag_readers empty whenever the store, the agent and the
    # write lived in three different files. Confirmed by testing.
    known_store_vars = {v for v in store_framework_by_var}

    llm_client_framework_by_var = {}
    for call_id, entry in pending_llm_clients.items():
        var_name = assign_target_for_call.get(call_id)
        if var_name:
            llm_client_framework_by_var[var_name] = entry["framework"]

    # Variable-to-variable aliasing: `worker = kb` should let `worker`
    # inherit whatever `kb` was already known to be, so a later
    # `worker.add(...)` still resolves to Chroma instead of being silently
    # dropped as unattributed. Confirmed as a real gap by testing (before
    # this fix, exactly this pattern produced zero write_sites at all).
    # Fixed-point loop, same idea as the file-import relevance propagation:
    # repeats until nothing new is added, so multi-hop chains (`a = kb;
    # b = a`) resolve regardless of source order. File-wide, not
    # function-scoped -- same simplification already true of the
    # underlying attribution dicts themselves.
    _resolve_variable_aliases(tree, store_framework_by_var, agent_framework_by_var, llm_client_framework_by_var)

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

    # write_sites: rag_writes-category call sites, but ONLY when the
    # receiver resolves to a CONFIRMED framework (via store_framework_by_var
    # -- i.e. this variable was actually created by a known vectorstore/
    # memory constructor somewhere earlier in this file). Verbs like
    # `.get(` are extremely generic -- without this filter, an ordinary
    # dict/object call (e.g. `runtime.get("api_key")` on a plain runtime
    # config object, nothing to do with any tracked framework) was showing
    # up as a "read" finding with framework=null. Confirmed as real noise
    # via testing against an actual repo. Unattributed sites are simply not
    # recorded now, rather than kept with a null framework.
    write_sites = []
    if rag_writes_category is not None:
        for node, func_ctx in _walk_with_function_context(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            method_text = f".{node.func.attr}("
            if _match_constructor_text(method_text, rag_writes_category, require_confirmation=False) is None:
                continue
            receiver = _resolve_identity(node.func)
            framework = store_framework_by_var.get(receiver)
            if framework is None:
                continue
            # NB: named `taint_source`, NOT `source` -- an earlier version
            # used `source` here, which silently shadowed the function's
            # own `source` parameter (the file's text) for the rest of the
            # function body. Harmless until something below actually
            # needed the real source text, at which point it crashed.
            tainted, taint_source = _is_write_argument_tainted(node, assigns_by_func_and_name, func_ctx)
            sanitized_nearby = _has_sanitizer_nearby(source_lines, node.lineno) if tainted else False
            write_sites.append({
                "line": node.lineno, "variable": receiver, "method": node.func.attr,
                "framework": framework,
                "tainted": tainted, "taint_source": taint_source,
                "sanitizer_nearby": sanitized_nearby,
                "unsanitized": tainted and not sanitized_nearby,
            })
    tainted_writes = [w for w in write_sites if w["unsanitized"]]

    # llm_tool_calls: Option A. Every call on a CONFIRMED OpenAI/Anthropic
    # client whose method looks like a real request (.create(/.stream(,
    # covering .chat.completions.create(, .messages.create(,
    # .messages.stream(, .responses.create()) and carries a `tools=`
    # keyword. This is what lets a fully custom, non-tracked-framework
    # agent (confirmed real-world case: NousResearch/hermes-agent) still
    # register real tool usage -- every implementation has to hand its
    # tools to the model through this exact parameter, however it built
    # them internally.
    llm_tool_calls = []
    for node, func_ctx in _walk_with_function_context(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in ("bind_tools", "bindTools"):
            # AST-based counterpart to _detect_bind_tools_calls' line regex
            # (JS/TS only) -- can actually look at the call's arguments.
            try:
                matched_call = f"{ast.unparse(node.func)}("
            except Exception:
                matched_call = f".{node.func.attr}("
            llm_tool_calls.append({
                "line": node.lineno, "variable": _resolve_identity(node.func), "framework": "LangChain",
                "matched_call": matched_call, "tool_names": _extract_bind_tools_names(node),
            })
            continue
        base_var = _resolve_identity(node.func)
        framework = llm_client_framework_by_var.get(base_var)
        if framework is None:
            continue
        request_path = _resolve_expr_text(node.func)[len(base_var) + 1:]
        if framework == "Mistral SDK":
            if request_path not in {"chat.complete", "chat.complete_async", "chat.stream", "chat.stream_async"}:
                continue
        elif framework.startswith("Custom ("):
            # An API key alone is not proof of an LLM client. Require a
            # recognizable request namespace as corroborating evidence.
            if request_path not in {"chat.completions.create", "messages.create", "messages.stream",
                                    "responses.create", "chat.complete", "chat.complete_async"}:
                continue
        elif node.func.attr not in LLM_TOOL_CALL_METHODS:
            continue
        tools_kwarg = next((kw.value for kw in node.keywords if kw.arg == "tools"), None)
        if tools_kwarg is None:
            tools_kwarg = _find_starred_tools_value(node, assigns_by_func_and_name, func_ctx)
        if tools_kwarg is None:
            continue
        tool_names = _extract_literal_tool_names(tools_kwarg)
        try:
            matched_call = f"{ast.unparse(node.func)}("  # e.g. "self.client.chat.completions.create(" -- the actual evidence primitive
        except Exception:
            matched_call = f".{node.func.attr}("
        llm_tool_calls.append({
            "line": node.lineno, "variable": base_var, "framework": framework,
            "matched_call": matched_call,
            "tool_names": tool_names,  # None means confirmed-but-not-extractable, see docstring above
        })

    tool_use_markers = _detect_tool_use_markers(source_lines)
    agent_markers = _detect_agent_markers(source_lines)

    # read_sites: same confirmed-framework-only filter as write_sites.
    read_sites = []
    if rag_reads_category is not None:
        for node, func_ctx in _walk_with_function_context(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method_text = f".{node.func.attr}("
            if _match_constructor_text(method_text, rag_reads_category, require_confirmation=False) is None:
                continue
            receiver = _resolve_identity(node.func)
            framework = store_framework_by_var.get(receiver)
            if framework is None:
                continue
            read_sites.append({
                "line": node.lineno, "variable": receiver, "method": node.func.attr,
                "framework": framework,
            })

    # call_sites: same confirmed-framework-only filter, resolved against
    # named_agents instead of named_stores.
    call_sites = []
    if agent_calls_category is not None:
        for node, func_ctx in _walk_with_function_context(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method_text = f".{node.func.attr}("
            if _match_constructor_text(method_text, agent_calls_category, require_confirmation=False) is None:
                continue
            receiver = _resolve_identity(node.func)
            framework = agent_framework_by_var.get(receiver)
            if framework is None:
                continue
            call_sites.append({
                "line": node.lineno, "variable": receiver, "method": node.func.attr,
                "framework": framework,
            })

    # imports: only kept for files that import AT LEAST ONE tracked
    # framework -- a file with no framework import at all contributes no
    # signal, so listing its (entirely stdlib/local) imports is pure noise.
    # When the file DOES qualify, every import is kept, framework or not,
    # so you can see the full picture of what that file pulls in.
    imports = []
    all_imports = []
    has_tracked_framework = False
    for local_name, canonical in sorted(import_aliases.items()):
        framework = _resolve_module_framework(canonical)
        if framework:
            has_tracked_framework = True
        all_imports.append({
            "local_name": local_name,
            "canonical": canonical,
            "framework": framework,
        })
    if has_tracked_framework:
        imports = all_imports

    return ("\n".join(lines), None, named_agents, named_stores,
            store_agent_links, tainted_writes, write_sites, read_sites, call_sites, imports,
            llm_tool_calls, tool_use_markers, agent_markers, confirmed_tool_definitions)


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
