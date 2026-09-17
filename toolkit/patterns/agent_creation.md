# Agent Creation Patterns

Scope: restricted to frameworks listed in Table II of the RADAR paper (39 frameworks
across Orchestration, LLM SDKs, Memory & RAG, Tool Use, Protocols). Frameworks not in
Table II (e.g. Microsoft Agent Framework, Genkit, FlowiseAI, Husk, Veryfront) have been
removed from this file — add them to Table II first if they should be tracked.

## Detection Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `detect_agent_creation(code, language)` | Detects all agent creation patterns | Dictionary with agent framework details |
| `count_agents(code, language)` | Counts agent instantiations | Integer count |
| `is_agent_framework(code, language)` | Quick boolean check for agent framework usage | True/False |
| `detect_agent_in_file(file_path, language)` | Analyzes file for agent creation | Dictionary with agent details |

---

## Python Agent Creation Patterns

| Framework | Detection Patterns |
|-----------|-------------------|
| **Agno** | `from agno.agent import Agent`<br>`agno\.Agent`<br>`Agent(`<br>`Team(`<br>`Workflow(`<br>`@agent`<br>`@crew`<br>`@tool`<br>`.run(`<br>`.arun(` |
| **Browser-use** | `Agent(` *(confirmed via `from browser_use import Agent`, same import-confirmation mechanism as every other bare "Agent(" row)* |
| **LangChain** | `create_agent(`<br>`create_react_agent(`<br>`create_json_agent(`<br>`create_openai_tools_agent(`<br>`create_tool_calling_agent(`<br>`create_structured_chat_agent(`<br>`initialize_agent(`<br>`AgentExecutor(`<br>`from langchain.agents import AgentExecutor` |
| **LangGraph** | `StateGraph(`<br>`MessageGraph(`<br>`.add_node(`<br>`.compile(`<br>`create_react_agent(` |
| **CrewAI** | `Agent(`<br>`@agent`<br>`@crew`<br>`Process.sequential`<br>`Process.hierarchical` |
| **AutoGen** | `ConversableAgent(`<br>`AssistantAgent(`<br>`UserProxyAgent(` |
| **LlamaIndex** | `ReActAgent(`<br>`OpenAIAgent(`<br>`FunctionCallingAgentWorker(`<br>`AgentRunner(`<br>`Workflow(`<br>`@step`<br>`StartEvent`<br>`StopEvent` |
| **Pydantic AI** | `pydantic_ai.Agent`<br>`from pydantic_ai import Agent`<br>`@agent.tool`<br>`@agent.tool_plain` |
| **Smolagents** | `ToolCallingAgent(`<br>`CodeAgent(`<br>`ManagedAgent(` |
| **Haystack** | `from haystack import Agent`<br>`Agent(`<br>`Pipeline(` |
| **OpenAI Agents SDK** | `from openai import Agent`<br>`from agents import Agent`<br>`openai.agents` |
| **Bee Agent Framework** | `from beeai_framework.agents.react import ReActAgent`<br>`from beeai_framework.agents.requirement import RequirementAgent`<br>`ReActAgent(`<br>`RequirementAgent(`<br>*(package was renamed from `bee_agent_framework` to `beeai_framework`; match both import roots)* |
| **Deep Agents** | `create_deep_agent(`<br>`from deepagents import create_deep_agent` *(the Python deepagents package -- same framework as JS's "Deep Agents JS" row below, canonicalized to one name via pattern_index.py's ALIASES, same as LangChain/LangChain.js)* |
| **Google ADK** | `Agent(`<br>`LlmAgent(`<br>`from google.adk` *(Google's Agent Development Kit -- resolved via a google.adk submodule override in pattern_detector.py, so it's never misread as Google GenAI just because both import under plain google)* |

---

## JavaScript/TypeScript Agent Creation Patterns

| Framework | Detection Patterns |
|-----------|-------------------|
| **LangChain.js** | `createReactAgent(`<br>`createOpenAIToolsAgent(`<br>`createToolCallingAgent(`<br>`new AgentExecutor(` |
| **LangGraph.js** | `new StateGraph(`<br>`.addNode(`<br>`.compile(` |
| **Mastra** | `new Mastra(`<br>`createAgent(`<br>`@mastra/core`<br>`new Agent<`<br>`MastraAgent` |
| **Vercel AI SDK** | `generateText(`<br>`streamText(`<br>`from 'ai'`<br>`from '@ai-sdk/` |
| **ElizaOS** | `createEliza(`<br>`ElizaAgent(`<br>`new AgentRuntime(`<br>`@elizaos/core` |
| **Deep Agents JS** | `createDeepAgent(`<br>`from "deepagents"`<br>`from 'deepagents'`<br>`\bDeepAgent\b`<br>`\bCreateDeepAgentParams\b` |
| **Bee Agent Framework** | `new ReActAgent(`<br>`new RequirementAgent(`<br>`@i-am-bee/beeai-framework`<br>`from 'beeai-framework'` |
| **CopilotKit** | `useCopilotAction(`<br>`<CopilotKit`<br>`@copilotkit/react-core` |
| **MCP SDK** | `new McpServer(`<br>`new Server(`<br>`@modelcontextprotocol/sdk` *(bare Server( is excluded — it's a substring of any unrelated createServer(/fooServer( call, confirmed a real false-positive source by testing; a bare "MCP" substring is excluded for the same reason — see Protocols note below)* |

---

## Generic Agent Creation Patterns (Framework-Agnostic)

Trimmed from 7 to 1 entry — the other 6 were functionally redundant with
patterns already listed under a specific framework above (bare `Agent(` is
already covered by Agno/CrewAI/Pydantic AI/Haystack/OpenAI Agents SDK;
`Crew(` by CrewAI; the AutoGen-class compound pattern by AutoGen's own three
rows; `GroupChat(` by AutoGen; `.add_node(` by LangGraph; the
`create\w*agent\(` factory catch-all by LangChain's explicit `create_*agent(`
rows) — keeping both just double-counts the same call, it doesn't add
detection coverage.

| Pattern Type | Regex Pattern | Why this one stays |
|-------------|---------------|--------------|
| Graph Compile | `\.compile\s*\(` | Not tied to any single framework's own row — a distinct "the graph is finalized" signal, not a bare constructor name shared with another framework. |


---

## Import-Based Detection

### Python Imports
```python
# Agno
from agno.agent import Agent
from agno.team import Team
from agno.workflow import Workflow
from agno.tools import tool

# LangChain
from langchain.agents import AgentExecutor, create_react_agent
from langchain.tools import tool

# LangGraph
from langgraph.graph import StateGraph, MessageGraph
from langgraph.checkpoint import MemorySaver

# CrewAI
from crewai import Agent, Crew, Process

# AutoGen
from autogen import AssistantAgent, UserProxyAgent, ConversableAgent, GroupChat

# Pydantic AI
from pydantic_ai import Agent

# LlamaIndex
from llama_index.core.agent import ReActAgent
from llama_index.core.workflow import Workflow, step, StartEvent, StopEvent

# Haystack
from haystack import Agent, Pipeline

# OpenAI Agents SDK
from agents import Agent

# Smolagents
from smolagents import ToolCallingAgent, CodeAgent, ManagedAgent

# Bee Agent Framework
from beeai_framework.agents.react import ReActAgent
from beeai_framework.agents.requirement import RequirementAgent

# Deep Agents
from deepagents import create_deep_agent

# Google ADK
from google.adk.agents import Agent, LlmAgent
```

### Notes on scope
- **MCP SDK** appears here only when it is used to *construct* an agent-serving process
  (`new McpServer(`, `new Server(`); a bare `MCP` or `mcptool` token is too generic for creation
  detection and belongs instead under the Protocols-specific handoff/call signals, gated
  by confirmation logic (see Agent Handoffs doc). It's tracked here for
  `framework_comparison`, but excluded from `n_agents` in pattern_detector.py --
  it creates a tool-exposing server, not an LLM agent.
- LLM SDKs without an "Agent" abstraction (OpenAI SDK, Anthropic SDK, Google GenAI,
  Together SDK, Instructor, js-agent) are intentionally excluded from this file — they
  provide model calls, not agent constructors. Their usage is picked up separately by
  the LLM-SDK detector, not here.
- `.bind_tools(`/`.bindTools(` (LangChain's own API for handing tools to a model) is
  also excluded here, for the opposite reason: called directly in a repo's own code
  (rather than inside one of the LangChain constructors above), it's evidence of a
  *custom*, hand-rolled agent, not "using the LangChain framework" — LangChain's own
  agent constructors call it internally, hidden inside the library, so seeing it in
  app code means that constructor was skipped. Counted as custom-agent evidence
  (`n_custom_agents`) in pattern_detector.py, not via this table.

## Verification

`patterns_verified.json` is generated from these tables; generation does not
verify API names. Deep Agents JS references checked on 2026-09-17:
[creation function](https://reference.langchain.com/javascript/deepagents/browser/createDeepAgent),
[types](https://reference.langchain.com/javascript/deepagents/types).
`DeepAgent`, `CreateDeepAgentParams`, and imports indicate usage; only the
`createDeepAgent(...)` call counts as a creation. `DeepAgentConfig` remains
unverified. Regression examples are in `pattern_scan/test_counts.py`.
