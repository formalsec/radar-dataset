"""
js_ast.py -- tree-sitter parsing and import resolution for JS/TS.

This is only the language layer: parse a file, say which local names were
imported from which package, and which framework a package belongs to.
Attribution (which call is an agent, which framework it is) lives next to
its Python counterpart in pattern_detector._reduce_javascript_ast, so both
languages share _match_constructor_text and the pattern registry.

Why tree-sitter: it parses JS, TS and TSX from Python with no Node
subprocess, and it never fails outright -- a syntax error becomes an ERROR
node and the rest of the tree is still usable, so one bad line doesn't
blank out a whole file the way ast.parse does for Python.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    import tree_sitter_javascript
    import tree_sitter_typescript
    from tree_sitter import Language, Parser
    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only outside the nix shell
    AVAILABLE = False


# .ts and .tsx need different grammars: parsing `<T>value` casts with the tsx
# grammar (or JSX with the typescript one) silently produces a wrong tree.
_GRAMMAR_BY_SUFFIX = {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}


@lru_cache(maxsize=None)
def _parser(grammar):
    if grammar == "typescript":
        language = tree_sitter_typescript.language_typescript()
    elif grammar == "tsx":
        language = tree_sitter_typescript.language_tsx()
    else:
        language = tree_sitter_javascript.language()
    return Parser(Language(language))


def parse(source, filename):
    """-> (root_node, parse_error).

    tree-sitter recovers from errors locally, and most of the errors it
    reports on real repos are grammar gaps rather than broken code (`&` in
    JSX text, `export type *`), so a file with a few ERROR nodes is still
    analysed and reported clean. parse_error is only set when ERROR nodes
    cover most of the file -- matching what it means for Python in
    scan_api's files_with_parse_errors: "this file wasn't really analysed"."""
    grammar = _GRAMMAR_BY_SUFFIX.get(Path(filename).suffix.lower(), "javascript")
    data = source.encode("utf-8")
    root = _parser(grammar).parse(data).root_node
    if not root.has_error:
        return root, None
    errors = [n for n in walk(root) if n.type == "ERROR"]
    error_bytes = sum(n.end_byte - n.start_byte for n in errors if not any(
        a.type == "ERROR" for a in _ancestors(n)))
    if error_bytes * 2 <= len(data):
        return root, None
    return root, f"SyntaxError: tree-sitter could not parse most of the file (first error at line {line(errors[0])})"


def _ancestors(node):
    node = node.parent
    while node is not None:
        yield node
        node = node.parent


def walk(node):
    """Pre-order walk over named nodes."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.named_children))


def text(node):
    return node.text.decode("utf-8", "replace")


def line(node):
    return node.start_point[0] + 1


def string_value(node):
    """A string literal's value, or None for anything dynamic (template
    substitutions, concatenation, identifiers)."""
    if node is None:
        return None
    if node.type == "string":
        return text(node)[1:-1]
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.named_children):
        return text(node)[1:-1]
    return None


def key(node):
    """Stable identity for a node across separate walks of the same tree."""
    return (node.start_byte, node.end_byte, node.type)


# Wrappers that don't change which value an expression evaluates to.
_TRANSPARENT = {
    "parenthesized_expression", "await_expression", "non_null_expression",
    "as_expression", "satisfies_expression", "type_assertion",
}


def unwrap(node):
    while node is not None and node.type in _TRANSPARENT:
        node = node.named_children[0] if node.type != "type_assertion" else node.named_children[-1]
    return node


# ---------------------------------------------------------------------- #
# Package -> framework
# ---------------------------------------------------------------------- #

# Exact package names, and scope/prefix entries ending in "/" or "-".
# A subpath (`langchain/agents`, `@langchain/langgraph/prebuilt`) resolves
# through its package; the longest matching entry wins, which is what lets
# @langchain/langgraph be LangGraph while every other @langchain/* package
# is LangChain -- same split as the Python langchain_* prefix rule.
JS_PACKAGE_TO_FRAMEWORK = {
    "langchain": "LangChain",
    "@langchain/": "LangChain",
    "@langchain/langgraph": "LangGraph",
    "@langchain/langgraph-": "LangGraph",  # -sdk, -cua, -supervisor, -swarm, ...
    "@mastra/": "Mastra",
    "ai": "Vercel AI SDK",
    "@ai-sdk/": "Vercel AI SDK",
    "@openai/agents": "OpenAI Agents SDK",
    "openai": "OpenAI SDK",
    "@anthropic-ai/sdk": "Anthropic SDK",
    "@anthropic-ai/claude-agent-sdk": "Claude Agent SDK",
    "@anthropic-ai/claude-code": "Claude Agent SDK",
    "@google/adk": "Google ADK",
    "@google/genai": "Google GenAI",
    "@google/generative-ai": "Google GenAI",
    "deepagents": "Deep Agents",
    "beeai-framework": "Bee Agent Framework",
    "@i-am-bee/beeai-framework": "Bee Agent Framework",
    "@elizaos/": "ElizaOS",
    "@copilotkit/": "CopilotKit",
    "@modelcontextprotocol/": "MCP SDK",
    "fastmcp": "FastMCP",
    "@a2a-js/sdk": "A2A SDK",
    "@earendil-works/pi-": "Pi",
    "@mariozechner/pi-": "Pi",
    "llamaindex": "LlamaIndex",
    "@llamaindex/": "LlamaIndex",
    "chromadb": "Chroma",
    "@pinecone-database/pinecone": "Pinecone",
    "@qdrant/js-client-rest": "Qdrant",
    "weaviate-client": "Weaviate",
    "weaviate-ts-client": "Weaviate",
    "@zilliz/milvus2-sdk-node": "Milvus",
    "@getzep/": "Zep",
    "mem0ai": "Mem0",
    "groq-sdk": "Groq SDK",
    "@mistralai/mistralai": "Mistral SDK",
    "together-ai": "Together SDK",
    "playwright": "Playwright",
    "e2b": "E2B",
    "@e2b/": "E2B",
    "composio-core": "Composio",
    "@composio/": "Composio",
    # TODO: extend as real repos surface more packages.
}


def package_framework(module):
    """Framework for an import specifier, or None (relative paths, unknown
    packages, Node builtins)."""
    if module is None or module.startswith("."):
        return None
    module = module.removeprefix("node:")
    best, best_len = None, -1
    for entry, framework in JS_PACKAGE_TO_FRAMEWORK.items():
        if entry.endswith(("/", "-")):
            hit = module.startswith(entry)
        else:
            hit = module == entry or module.startswith(entry + "/")
        if hit and len(entry) > best_len:
            best, best_len = framework, len(entry)
    return best


# ---------------------------------------------------------------------- #
# Imports
# ---------------------------------------------------------------------- #

@dataclass(frozen=True)
class ImportBinding:
    module: str
    export: str | None  # None = namespace/whole module; "default" = default export
    line: int
    node_key: tuple  # the binding identifier, so it isn't mistaken for a shadow


def _require_module(node):
    """`require("x")` / `import("x")` -> "x", else None."""
    node = unwrap(node)
    if node is None or node.type != "call_expression":
        return None
    func = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if func is None or args is None or len(args.named_children) != 1:
        return None
    if not (func.type == "import" or (func.type == "identifier" and text(func) == "require")):
        return None
    return string_value(args.named_children[0])


def _is_type_only(node):
    return any(c.type == "type" for c in node.children)


def build_import_aliases(root):
    """
    local name -> ImportBinding. Covers:
        import X from "m"                    X -> (m, "default")
        import * as ns from "m"              ns -> (m, None)
        import { A, B as C } from "m"        A -> (m, "A"), C -> (m, "B")
        import X = require("m")              (TS) X -> (m, None)
        const X = require("m")               X -> (m, None)
        const { A, B: C } = require("m")     A -> (m, "A"), C -> (m, "B")
        const A = require("m").A             A -> (m, "A")
        const X = await import("m")          X -> (m, None)
    Type-only imports are skipped: they never exist at runtime.
    """
    aliases = {}

    def bind(name_node, module, export):
        aliases[text(name_node)] = ImportBinding(module, export, line(name_node), key(name_node))

    for node in walk(root):
        if node.type == "import_statement" and not _is_type_only(node):
            module = string_value(node.child_by_field_name("source"))
            for clause in node.named_children:
                if clause.type == "import_require_clause":
                    ident = clause.named_children[0]
                    bind(ident, string_value(clause.child_by_field_name("source")), None)
                if clause.type != "import_clause" or module is None:
                    continue
                for part in clause.named_children:
                    if part.type == "identifier":
                        bind(part, module, "default")
                    elif part.type == "namespace_import":
                        bind(part.named_children[0], module, None)
                    elif part.type == "named_imports":
                        for spec in part.named_children:
                            if spec.type != "import_specifier" or _is_type_only(spec):
                                continue
                            name = spec.child_by_field_name("name")
                            alias = spec.child_by_field_name("alias") or name
                            bind(alias, module, string_value(name) if name.type == "string" else text(name))
        elif node.type == "variable_declarator":
            target = node.child_by_field_name("name")
            value = unwrap(node.child_by_field_name("value"))
            if target is None or value is None:
                continue
            export = None
            if value.type == "member_expression":
                prop = value.child_by_field_name("property")
                export = text(prop) if prop is not None else None
                value = value.child_by_field_name("object")
            module = _require_module(value)
            if module is None:
                continue
            if target.type == "identifier":
                bind(target, module, export)
            elif target.type == "object_pattern" and export is None:
                for prop in target.named_children:
                    if prop.type == "shorthand_property_identifier_pattern":
                        bind(prop, module, text(prop))
                    elif prop.type == "pair_pattern":
                        k, v = prop.child_by_field_name("key"), prop.child_by_field_name("value")
                        if v is not None and v.type == "assignment_pattern":
                            v = v.child_by_field_name("left")
                        if v is not None and v.type == "identifier":
                            bind(v, module, string_value(k) if k.type == "string" else text(k))
    return aliases


# ---------------------------------------------------------------------- #
# Shadowing
# ---------------------------------------------------------------------- #

_SCOPE_TYPES = {
    "program", "statement_block", "class_body", "catch_clause",
    "function_declaration", "function_expression", "function", "arrow_function",
    "generator_function_declaration", "generator_function", "method_definition",
    "for_statement", "for_in_statement",
}
_FUNCTION_TYPES = {
    "function_declaration", "function_expression", "function", "arrow_function",
    "generator_function_declaration", "generator_function", "method_definition",
}


def _pattern_names(node):
    """Identifier nodes bound by a declaration/parameter pattern."""
    if node is None:
        return []
    if node.type in ("identifier", "shorthand_property_identifier_pattern"):
        return [node]
    if node.type == "pair_pattern":
        return _pattern_names(node.child_by_field_name("value"))
    if node.type in ("assignment_pattern", "object_assignment_pattern"):
        return _pattern_names(node.child_by_field_name("left") or node.named_children[0])
    if node.type in ("required_parameter", "optional_parameter"):
        return _pattern_names(node.child_by_field_name("pattern"))
    if node.type in ("object_pattern", "array_pattern", "rest_pattern", "formal_parameters"):
        return [n for c in node.named_children for n in _pattern_names(c)]
    return []


def _enclosing_scope(node):
    node = node.parent
    while node is not None and node.type not in _SCOPE_TYPES:
        node = node.parent
    return node


def build_shadows(root, aliases):
    """
    Scope node key -> set of imported names re-declared inside that scope.
    A parameter, local variable, function or class with an imported name
    hides the import for everything inside that scope -- `function f(query)
    { query(...) }` is not the Claude Agent SDK's query(). Reassigning a
    require()-bound name (`Agent = Other`) hides it everywhere, since the
    binding itself changed. Approximation: hoisting and TDZ are ignored.
    """
    import_keys = {b.node_key for b in aliases.values()}
    shadows = {}

    def shadow(scope, ident):
        name = text(ident)
        if name in aliases and key(ident) not in import_keys and scope is not None:
            shadows.setdefault(key(scope), set()).add(name)

    for node in walk(root):
        t = node.type
        if t == "variable_declarator":
            for ident in _pattern_names(node.child_by_field_name("name")):
                shadow(_enclosing_scope(node), ident)
        elif t in ("function_declaration", "generator_function_declaration", "class_declaration"):
            name = node.child_by_field_name("name")
            if name is not None:
                shadow(_enclosing_scope(node), name)
        if t in _FUNCTION_TYPES:
            params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
            for ident in _pattern_names(params):
                shadow(node, ident)
        if t == "catch_clause":
            for ident in _pattern_names(node.child_by_field_name("parameter")):
                shadow(node, ident)
        if t == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                shadow(root, left)
    return shadows


def visible_binding(ident_node, aliases, shadows):
    """The import binding an identifier refers to, or None if it isn't an
    import or a closer declaration hides it."""
    name = text(ident_node)
    binding = aliases.get(name)
    if binding is None:
        return None
    node = ident_node
    while node is not None:
        if name in shadows.get(key(node), ()):
            return None
        node = node.parent
    return binding
