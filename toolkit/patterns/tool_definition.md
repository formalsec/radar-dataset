# Tool Definition Patterns

Python decorators and constructors feed confirmed tool counts through framework
resolution. Most JS/TS definitions remain unconfirmed markers, EXCEPT the two
shapes documented under "Confirmed JS/TS Detection" below, which are wired into
`n_tools` the same way Python's decorators/constructors are. Server construction
and MCP request handlers are not individual tool definitions.

---

## Python Tool Definition Patterns

| Framework | Detection Patterns |
|-----------|-------------------|
| **LangChain** | `@tool`<br>`StructuredTool.from_function(`<br>`from langchain_core.tools import tool`<br>`from langchain.tools import tool` |
| **CrewAI** | `@tool`<br>`BaseTool`<br>`from crewai.tools import tool`<br>`from crewai_tools import tool` |
| **OpenAI Agents SDK** | `@function_tool`<br>`FunctionTool(`<br>`from agents import function_tool` |
| **Agno** | `@tool` |
| **Pydantic AI** | `@agent.tool`<br>`@agent.tool_plain` |
| **Google ADK** | `FunctionTool(`<br>`from google.adk.tools import FunctionTool` |
| **LlamaIndex** | `FunctionTool.from_defaults(`<br>`QueryEngineTool(`<br>`from llama_index.core.tools import FunctionTool` |
| **Smolagents** | `@tool`<br>`from smolagents import tool` |
| **MCP SDK** | `@mcp.tool`<br>`from mcp.server.fastmcp import FastMCP` |
| **Browser-use** | `@controller.action`<br>`from browser_use import Controller` |

---

## JavaScript/TypeScript Tool Definition Patterns

| Framework | Detection Patterns |
|-----------|-------------------|
| **LangChain.js** | `new DynamicStructuredTool(`<br>`new DynamicTool(` |
| **Mastra** | `createTool(`<br>`from '@mastra/core/tools'` |
| **CopilotKit** | `useCopilotAction(` |
| **MCP SDK** | `server.tool(`<br>`server.setRequestHandler(ListToolsRequestSchema`<br>`server.setRequestHandler(CallToolRequestSchema` |

---

## Import-Based Detection

### Python Imports
```python
# LangChain
from langchain_core.tools import tool
from langchain.tools import tool, StructuredTool

# CrewAI
from crewai.tools import tool
from crewai_tools import tool, BaseTool

# OpenAI Agents SDK
from agents import function_tool, FunctionTool

# Google ADK
from google.adk.tools import FunctionTool

# LlamaIndex
from llama_index.core.tools import FunctionTool, QueryEngineTool

# Smolagents
from smolagents import tool, Tool

# MCP SDK (FastMCP)
from mcp.server.fastmcp import FastMCP

# Browser-use
from browser_use import Controller
```

### Notes on scope

- Bare `Tool(` and `tool(` are excluded to avoid unrelated classes and helpers.
- Existing overlapping agent patterns remain unchanged.
- MCP server constructors and request handlers do not count as individual tools.

---

## Confirmed JS/TS Detection

Two shapes, implemented in `pattern_detector.py`, are confirmed the same way
Python's decorators/constructors are (they feed `n_tools`, not just the
unconfirmed marker tier). Both require an actual usage context, not object
shape alone -- see `WORLDMONITOR_TOOL_DETECTION.md` for the finding that
motivated this (a real MCP server whose tools were plain objects, not
decorators/constructors, and were completely invisible).

| Pattern | Confirming context | Notes |
|---------|--------------------|-------|
| Plain-object MCP tool registry (`{name, description, inputSchema}`) | A `tools:` catalog value or named registry lookup in a file with a non-comment MCP dispatch marker | Follows the served value through bindings, relative imports, spreads and catalog projections such as `TOOL_LIST_RESPONSE = TOOL_REGISTRY.map(...)`. The registry need not be declared in the handler file. Unused and unrelated sibling registries do not qualify. |
| WebMCP tool registration (`provider.registerTool(tool, ...)`) | The call's argument resolves to a tool object, directly or through bounded local data flow | Follows scoped bindings, named wrapper parameters, parenthesized `map`/`forEach` callbacks, braced `for (const tool of tools)` loops and factory returns, up to 12 hops. Resolves local/imported name constants, including `Object.freeze({...})`. Unconnected arrays and unsupported expressions remain unconfirmed. |

Shape check for both (`_js_tool_def_object_name`): requires a `name` key
plus at least one of `description`/`execute`/`inputSchema`/`parameters`/`schema`.
Object shape alone never confirms anything on its own -- both callers only
invoke it once their own registration/dispatch context already matched.

## Verification

`pattern_index.py` generates the `tool_definition` category from these tables.
Python matches use framework resolution; unconfirmed markers are reported
separately and do not contribute to `n_tools`. Tool names are deduplicated per repo.
Regression tests are in `pattern_scan/tests/test_detection_regressions.py`
(`JsToolDefinitionRegressions`), including the imported catalog and two-wrapper
factory shapes used by WorldMonitor, unrelated arrays/registries, shadowed
parameters, cycles, constant values and deduplication.

A full tarball scan of `koala73/worldmonitor` at
`8a40613ec51b860f2c859acc5283c697602d09f6` now reports **108 tools**
(75 server tools, 33 browser tools), **108 confirmed definitions**, and
**0 agents**, across 2,655 scanned files with no parse errors. The earlier
109-tool count included the unrelated MCP resource `Seed-Meta Freshness`.
Browser names now use their actual constant values, e.g. `focus_country`.
