import json
import re
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).parent
_DEFAULT_SOURCE_DIR = _PACKAGE_DIR
_DEFAULT_OUT_PATH = _PACKAGE_DIR / "data" / "patterns_verified.json"

MASTER_FRAMEWORKS = [
    "LangChain", "LangGraph", "LlamaIndex", "CrewAI", "AutoGen", "Mastra",
    "Vercel AI SDK", "ElizaOS", "Bee Agent Framework", "Smolagents",
    "Pydantic AI", "Agno", "Haystack", "OpenAI Agents SDK", "Deep Agents",
    "Google ADK", "CAMEL", "Claude Agent SDK", "Pi",
    "OpenAI SDK", "Anthropic SDK", "Google GenAI", "Together SDK",
    "Instructor", "js-agent", "CopilotKit",
    "Mem0", "Chroma", "Weaviate", "Qdrant", "Pinecone", "Zep", "PGVector", "Milvus",
    "Playwright", "Browser-use", "Puppeteer", "Stagehand", "E2B", "Composio",
    "MCP SDK", "FastMCP", "A2A SDK",
]
MASTER_SET = set(MASTER_FRAMEWORKS)

ALIASES = {
    "Deep Agents JS": "Deep Agents",
    "LangChain.js": "LangChain", "LangChain JS": "LangChain",
    "LangChain JS RAG": "LangChain", "LangChain RAG": "LangChain",
    "LangGraph.js": "LangGraph", "LangGraph JS": "LangGraph",
    "Chroma JS": "Chroma", "Pinecone JS": "Pinecone",
    "Qdrant JS": "Qdrant", "Weaviate JS": "Weaviate",
    "LlamaIndex RAG": "LlamaIndex", "Haystack RAG": "Haystack",
    "Agno RAG": "Agno", "Vercel AI RAG": "Vercel AI SDK",
    "Vercel AI": "Vercel AI SDK",
    "OpenAI": "OpenAI SDK", "Google": "Google GenAI",
}

CATEGORY_FILES = {
    "agent_creation": "patterns/agent_creation.md",
    "agent_calls": "patterns/agent_calls.md",
    "rag_creation": "patterns/rag_creation.md",
    "rag_writes": "patterns/rag_writes.md",
    "rag_reads": "patterns/rag_reads.md",
    "a2a_interaction": "patterns/a2a_interaction.md",
    "tool_definition": "patterns/tool_definition.md",
}

BACKTICK_RE = re.compile(r"`([^`]+)`")
BOLD_WRAP_RE = re.compile(r"^\*\*(.+)\*\*$")
BACKTICK_WRAP_RE = re.compile(r"^`(.+)`$")


def _normalize_label(raw_label):
    label = BOLD_WRAP_RE.sub(r"\1", raw_label.strip())
    label = BACKTICK_WRAP_RE.sub(r"\1", label.strip())
    return label.strip()


def _resolve_framework(label):
    if label in MASTER_SET:
        return label, True
    if label in ALIASES:
        return ALIASES[label], True
    return label, False


def _parse_row(line, fallback_label):
    line = line.strip()
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip("|").split("|")]
    if len(cells) < 2:
        return None

    first_cell, second_cell = cells[0], cells[1]
    patterns_in_second = BACKTICK_RE.findall(second_cell)
    if patterns_in_second:
        label = _normalize_label(first_cell)
        return label, patterns_in_second

    patterns_in_first = BACKTICK_RE.findall(first_cell)
    if patterns_in_first:
        return fallback_label, patterns_in_first

    return None


def _split_sections(text):
    heading = None
    body = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                yield heading, body
            heading = line[3:].strip()
            body = []
        else:
            body.append(line)
    if heading is not None:
        yield heading, body


def _heading_language(heading):
    """Tags a framework's patterns with the language its heading is scoped
    to, so e.g. Agno's Python-only `Agent(` never gets swept against JS/TS
    text. Other headings (Generic, Import-Based, ...) stay unrestricted."""
    if heading.startswith("Python"):
        return "python"
    if heading.startswith("JavaScript"):
        return "javascript"
    return None


def parse_file(path, category):
    frameworks = {}
    generic = {}
    framework_languages = {}
    fallback_label = f"{category}_api_patterns"

    for heading, body in _split_sections(Path(path).read_text(encoding="utf-8")):
        if "detection methods" in heading.lower():
            continue
        heading_language = _heading_language(heading)
        for line in body:
            parsed = _parse_row(line, fallback_label)
            if parsed is None:
                continue
            label, patterns = parsed
            canonical, is_framework = _resolve_framework(label)
            bucket = frameworks if is_framework else generic
            bucket.setdefault(canonical, set()).update(patterns)
            if is_framework and heading_language is not None:
                framework_languages.setdefault(canonical, set()).add(heading_language)

    return frameworks, generic, framework_languages


def build_index(src_dir=".", out_path="patterns_verified.json"):
    src_dir = Path(src_dir)
    index = {"categories": {}}
    print("Building patterns_verified.json from the six category files:\n")

    for category, filename in CATEGORY_FILES.items():
        path = src_dir / filename
        if not path.exists():
            raise SystemExit(f"Missing {path} -- pass the directory containing all six .md files.")
        frameworks, generic, framework_languages = parse_file(path, category)
        index["categories"][category] = {
            "frameworks": {k: sorted(v) for k, v in sorted(frameworks.items())},
            "generic": {k: sorted(v) for k, v in sorted(generic.items())},
            "framework_languages": {k: sorted(v) for k, v in sorted(framework_languages.items())},
        }
        n_fw_patterns = sum(len(v) for v in frameworks.values())
        n_generic_patterns = sum(len(v) for v in generic.values())
        print(f"  {category:18s} {len(frameworks):2d} frameworks ({n_fw_patterns:3d} patterns)  "
              f"| {len(generic):2d} generic buckets ({n_generic_patterns:3d} patterns)")

    Path(out_path).write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return index


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else str(_DEFAULT_SOURCE_DIR)
    out = sys.argv[2] if len(sys.argv) > 2 else str(_DEFAULT_OUT_PATH)
    build_index(src, out)