# Tool Definition Patterns

Python decorators and constructors feed confirmed tool counts through framework
resolution. JS/TS definitions remain unconfirmed markers. Server construction and
MCP request handlers are not individual tool definitions.

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

## Verification

`pattern_index.py` generates the `tool_definition` category from these tables.
Python matches use framework resolution; unconfirmed markers are reported
separately and do not contribute to `n_tools`. Tool names are deduplicated per repo.
