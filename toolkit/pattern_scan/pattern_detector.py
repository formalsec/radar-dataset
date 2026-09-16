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
    "google": "Google GenAI",  # covers google.genai / google.generativeai
    "together": "Together SDK",
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
    custom_agents: list = field(default_factory=list)  # confirmed hand-rolled agents, see _group_custom_agents


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

    def analyze_source(self, source, filename, size_bytes=None, external_exports=None):
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
        if language == "python":
            reduced = _reduce_python(
                source, self._agent_creation_category,
                self._rag_creation_category, self._rag_writes_category,
                self._rag_reads_category, self._agent_calls_category,
                external_exports=external_exports,
            )
            (reduced_text, parse_error, named_agents, named_stores,
             store_links, tainted_writes, write_sites, read_sites, call_sites, imports,
             llm_tool_calls, tool_use_markers, agent_markers) = reduced
        else:
            reduced_text, parse_error = _reduce_javascript(source), None
            named_agents, named_stores, store_links, tainted_writes, write_sites, read_sites, call_sites, imports = [], [], [], [], [], [], [], []
            # No AST reduction for JS/TS -- plain-text detectors only.
            llm_tool_calls = _detect_bind_tools_calls(source_lines)
            tool_use_markers = _detect_tool_use_markers(source_lines)
            agent_markers = _detect_agent_markers(source_lines)

        result = FileResult(
            language=language, parse_error=parse_error,
            named_agents=named_agents, named_stores=named_stores,
            store_agent_links=store_links, tainted_writes=tainted_writes,
            write_sites=write_sites, read_sites=read_sites, call_sites=call_sites,
            imports=imports, llm_tool_calls=llm_tool_calls,
            tool_use_markers=tool_use_markers, agent_markers=agent_markers,
            custom_agents=_group_custom_agents(llm_tool_calls),
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
                "line_content": _line_content(a["line"]),
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
        if not isinstance(source, ast.Dict):
            continue
        for k, v in zip(source.keys, source.values):
            if isinstance(k, ast.Constant) and k.value == "tools":
                return v
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
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            target = node.targets[0].id
            source_var = node.value.id
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
        module_root = canonical.split(".")[0]
    elif isinstance(func_node, ast.Attribute) and isinstance(func_node.value, ast.Name):
        base = func_node.value.id
        canonical = import_aliases.get(base)
        module_root = canonical.split(".")[0] if canonical else base
    else:
        return set()
    fw = MODULE_TO_FRAMEWORK.get(module_root)
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
    "Anthropic": "Anthropic SDK",
    "AsyncAnthropic": "Anthropic SDK",
}

# Option A: tools are only reliably detectable for a CUSTOM (non-tracked-
# framework) agent at the point they're actually handed to the LLM API --
# every real implementation, however it's built internally, has to funnel
# its tools through the SDK's own `tools=` parameter to reach the model at
# all. This is confirmed the same way as everything else: the receiving
# client must trace back to a real OpenAI/Anthropic SDK import.
LLM_TOOL_CALL_METHODS = {"create", "stream"}  # .chat.completions.create(, .messages.create(, .messages.stream(


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
            name = (call.func.id if isinstance(call.func, ast.Name)
                    else call.func.attr if isinstance(call.func, ast.Attribute)
                    else None)
            if name not in LLM_CLIENT_CONSTRUCTORS:
                continue
            call_frameworks = _frameworks_for_call_identifier(call.func, import_aliases)
            if LLM_CLIENT_CONSTRUCTORS[name] in call_frameworks:
                factories[fn.name] = LLM_CLIENT_CONSTRUCTORS[name]
                break
    return factories


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



def _scan_line_markers(source_lines, pattern):
    """Plain per-line regex scan -> [{line, matched}]. One hit per line
    (the first match), so a single line can't flood the output."""
    hits = []
    for idx, text in enumerate(source_lines, start=1):
        m = pattern.search(text)
        if m:
            hits.append({"line": idx, "matched": m.group(0).strip()})
    return hits


def _detect_tool_use_markers(source_lines):
    return _scan_line_markers(source_lines, _TOOL_USE_MARKER_PATTERN)


def _detect_agent_markers(source_lines):
    return _scan_line_markers(source_lines, _AGENT_MARKER_PATTERN)


# `.bind_tools(`/`.bindTools(` -- LangChain's API for handing tools to a
# model outside its packaged agent constructors.
_BIND_TOOLS_RE = re.compile(r"\b(\w+)\.(?:bind_tools|bindTools)\s*\(")


def _detect_bind_tools_calls(source_lines):
    hits = []
    for idx, text in enumerate(source_lines, start=1):
        m = _BIND_TOOLS_RE.search(text)
        if m:
            hits.append({
                "line": idx, "variable": m.group(1), "framework": "LangChain",
                "matched_call": m.group(0).strip(), "tool_names": None,
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


def _reduce_python(source, agent_creation_category, rag_creation_category, rag_writes_category, rag_reads_category=None, agent_calls_category=None, external_exports=None):
    """
    Returns (reduced_text, parse_error, named_agents, named_stores,
    store_agent_links, tainted_writes).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        return source, f"{type(e).__name__}: {e}", [], [], [], [], [], [], [], [], [], [], [], []
    except RecursionError as e:
        return source, f"RecursionError: {e}", [], [], [], [], [], [], [], [], [], [], [], []

    source_lines = source.splitlines()
    import_aliases = _build_import_aliases(tree)
    extra_agent_classes, extra_store_classes = _build_subclass_extensions(
        tree, agent_creation_category, rag_creation_category, import_aliases
    )
    llm_client_factory_functions = _build_llm_client_factory_functions(tree, import_aliases)

    lines = []
    pending_agent_calls = {}
    pending_store_calls = {}
    pending_llm_clients = {}
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
            call_frameworks = _frameworks_for_call_identifier(node.func, import_aliases)
            # Canonical (fully-qualified) form of this call, via the file's
            # own imports -- lets a locally-renamed import still match a
            # registry pattern written in qualified form. See
            # _canonical_call_text.
            canonical_text = _canonical_call_text(node.func, import_aliases)
            framework = None
            if agent_creation_category is not None:
                framework = _match_constructor_text(call_text, agent_creation_category, call_frameworks)
                if framework is None and canonical_text:
                    framework = _match_constructor_text(canonical_text, agent_creation_category, call_frameworks)
                if framework is not None and _is_non_agent_compile_call(node.func, import_aliases):
                    framework = None
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

            # Store creation: same role-first order as above.
            store_framework = None
            if rag_creation_category is not None:
                store_framework = _match_constructor_text(call_text, rag_creation_category, call_frameworks)
                if store_framework is None and canonical_text:
                    store_framework = _match_constructor_text(canonical_text, rag_creation_category, call_frameworks)
            if store_framework is None and isinstance(node.func, ast.Name):
                store_framework = extra_store_classes.get(node.func.id)
            if store_framework is not None:
                pending_store_calls[id(node)] = {
                    "framework": store_framework,
                    "line": node.lineno,
                    "call_node": node,
                    "matched_call": call_text,
                }

            # LLM SDK client creation (Option A foundation): confirms which
            # variable is a real OpenAI/Anthropic client, so a later
            # `.chat.completions.create(tools=...)` on it can be trusted as
            # genuine tool-calling evidence rather than a guess. Same
            # per-identifier import confirmation as everything else here.
            llm_client_name = (
                node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else None
            )
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
            # "variable" is the ASSIGNMENT TARGET, kept separate from "name"
            # (which prefers a name=/role=/id= kwarg for display). Only the
            # variable is importable from another module, so cross-file
            # export keying must use this -- keying on "name" meant
            # `researcher = Crew(role="analyst")` was exported as "analyst"
            # and `from agents import researcher` could never match it.
            # Confirmed by testing.
            "variable": var_name,
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
        if node.func.attr not in LLM_TOOL_CALL_METHODS:
            continue
        base_var = _resolve_identity(node.func)
        framework = llm_client_framework_by_var.get(base_var)
        if framework is None:
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

    llm_tool_calls.extend(_detect_bind_tools_calls(source_lines))

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
        module_root = canonical.split(".")[0]
        framework = MODULE_TO_FRAMEWORK.get(module_root)
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
            llm_tool_calls, tool_use_markers, agent_markers)


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