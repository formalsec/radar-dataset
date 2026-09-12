# Agent Calls Patterns

Scope: restricted to frameworks listed in Table II of the RADAR paper. Microsoft Agent
Framework and Husk have been removed (not in Table II). Framework-agnostic A2A signals
are kept here for call-level detection only; framework-specific delegation/handoff
primitives now live in `a2a_interaction.md`.

## Detection Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `detect_agent_calls(code, language)` | Detects all agent invocation patterns | Dictionary with call details |
| `count_agent_calls(code, language)` | Counts agent invocations | Integer count |
| `detect_agent_to_agent_calls(code, language)` | Detects A2A (agent-to-agent) calls | List of A2A interactions |

---

## Python Agent Call Patterns

| Framework | Call Patterns |
|-----------|--------------|
| **LangChain** | `.invoke(`<br>`.ainvoke(`<br>`.stream(` |
| **LangGraph** | `.invoke(`<br>`.ainvoke(`<br>`.stream(` |
| **CrewAI** | `.kickoff(` |
| **AutoGen** | `.initiate_chat(`<br>`.initiate_chats(` |
| **OpenAI Agents SDK** | `agent.run(`<br>`Runner.run(`<br>`Runner.run_sync(` |
| **Agno** | `.run(`<br>`.arun(` |
| **LlamaIndex** | `.chat(`<br>`.achat(`<br>`.query(` |
| **Pydantic AI** | `.run_sync(`<br>`.run(` |
| **Smolagents** | `.run(` (via `CodeAgent(...).run(task)` / `ToolCallingAgent(...).run(task)`) |
| **Haystack** | `.run(` (both `Pipeline.run(` and `Agent.run(` — disambiguate by preceding constructor in the same scope) |
| **Bee Agent Framework** | `.run(`<br>`await agent.run(` |

---

## JavaScript/TypeScript Agent Call Patterns

| Framework | Call Patterns |
|-----------|--------------|
| **LangChain.js** | `.invoke(`<br>`.stream(` |
| **LangGraph.js** | `.invoke(`<br>`.stream(` |
| **Mastra** | `.run(` |
| **Vercel AI SDK** | `generateText(`<br>`streamText(` |
| **ElizaOS** | `.run(`<br>`runtime.processActions(` |
| **Deep Agents JS** | `.invoke(`<br>`.stream(` (inherits LangGraph call surface) |
| **Bee Agent Framework** | `.run(`<br>`await agent.run(` |

---

## Generic Agent Call Patterns (Framework-Agnostic)

| Pattern Type | Regex Pattern |
|-------------|---------------|
| Direct Invoke | `\.invoke\s*\(` |
| Async Invoke | `\.ainvoke\s*\(` |
| Stream | `\.stream\s*\(` |
| Run | `\.run\s*\(` |
| Async Run | `\.arun\s*\(` |
| Sync Run | `\.run_sync\s*\(` |
| Chat Initiation | `\.initiate_chat\s*\(` |
| Kickoff | `\.kickoff\s*\(` |
| Chat | `\.chat\s*\(` |

---

## API-Based Agent Calls

| API Pattern | Description |
|-------------|-------------|
| `POST /api/agents/` | Agent API endpoint |
| `POST /api/agents/[^/]+/invoke` | Agent invocation endpoint |
| `X-Agent-ID` | Agent identification header |

Note: `POST /api/memories` used to be listed here too, and the "Agent-to-Agent
(A2A) Call Patterns" section used to sit here as well — both removed. A
memory-creation endpoint is a **write**, not an agent call (now lives only in
`rag_writes.md`), and the A2A patterns were duplicating/fragmenting content
that belongs entirely in `a2a_interaction.md`. `delegate_to_agent(` and
`sub_agent.run(` were literally duplicated in both files; the rest
(`invoke_agent(`, `agent.invoke_child(`, `tool.invoke_agent`, `A2A`,
`agent-to-agent`) existed only here and nowhere in `a2a_interaction.md` at
all — moved there now so this concept has exactly one home.