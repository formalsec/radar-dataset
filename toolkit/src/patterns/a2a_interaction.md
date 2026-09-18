# Agent Handoff / A2A Interaction Patterns

Scope: restricted to frameworks listed in Table II of the RADAR paper. This file covers
`has_agent_handoffs` (Section IV-A) — structured delegation between agents built into a
specific framework's API, PLUS the framework-agnostic A2A signals that used to live in
`agent_calls.md` (moved here, since this is their correct single home — see
`agent_calls.md`'s note on that move).

## Detection Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `detect_agent_handoffs(code, language)` | Detects structured delegation between agents | Dictionary with handoff details |
| `count_handoffs(code, language)` | Counts distinct handoff/delegation sites | Integer count |
| `detect_handoff_targets(code, language)` | Resolves which agent a handoff routes to | List of (source, target) pairs |

---

## Python Handoff Patterns

| Framework | Handoff Patterns | Mechanism |
|-----------|-------------------|-----------|
| **OpenAI Agents SDK** | `handoffs=[`<br>`Handoff(` | Explicit handoff list passed to an agent constructor |
| **LangGraph** | `.add_edge(`<br>`Command(goto=`<br>conditional edge functions returning a node name | Graph edges / conditional routing between nodes |
| **AutoGen** | `GroupChat(`<br>`.initiate_chat(`<br>`speaker_selection_method=` | Multi-agent conversation with a selectable next speaker |
| **CrewAI** | `Process.hierarchical`<br>`manager_agent=`<br>`manager_llm=` | Manager agent delegates tasks to crew members |
| **Bee Agent Framework** | `HandoffTool(`<br>`from beeai_framework.tools.handoff import HandoffTool` | Dedicated handoff tool wired into a `RequirementAgent` |
| **Agno** | `Team(`<br>`mode="coordinate"`<br>`mode="route"` | Team-level delegation across member agents |
| **LlamaIndex** | `.add_workflows(`<br>`AgentWorkflow(`<br>handoff between `@step`-decorated functions via emitted events | Workflow-level step-to-step routing |
| **Smolagents** | `ManagedAgent(`<br>manager agent's `managed_agents=[` | Manager agent delegates to managed sub-agents |

---

## JavaScript/TypeScript Handoff Patterns

| Framework | Handoff Patterns | Mechanism |
|-----------|-------------------|-----------|
| **LangGraph.js** | `.addEdge(`<br>`Command({ goto:` | Graph edges / conditional routing |
| **Mastra** | `.getAgent(` called from inside another agent's tool<br>`Workflow(`<br>`.step(` chaining | Workflow-based handoff between steps/agents |
| **Deep Agents JS** | inherits LangGraph.js edge patterns (built on `@langchain/langgraph`) | Graph-based delegation |
| **ElizaOS** | `runtime.processActions(`<br>action handlers that dispatch to another registered agent/plugin | Action-dispatch style delegation |

---

## Protocol-Level A2A Patterns

| Protocol | Patterns | Notes |
|----------|----------|-------|
| **A2A SDK** | `A2AClient(`<br>`.send_task(`<br>`.get_task(`<br>agent-card discovery (`/.well-known/agent.json` fetch) | Cross-process agent-to-agent delegation over the A2A protocol; gate the generic `A2A` / `agent-to-agent` string match in `agent_calls.md` on co-occurrence with one of these calls or the `a2a-sdk` import to avoid matching unrelated prose/comments |
| **MCP SDK** | tool call that itself returns control to a different agent/session, `sampling/createMessage` request from a server back to the client | MCP-mediated handoff is rarer and typically indirect — treat as low-confidence unless paired with an explicit multi-agent constructor elsewhere in the file |

---

## Generic Handoff Patterns (Framework-Agnostic)

Trimmed, and merged with patterns migrated from `agent_calls.md` (see that
file's note). `handoffs=[`, `managed_agents=`, `Command(goto=`, `.add_edge(`,
`manager_agent=`, `manager_llm=` were removed — each is already listed
literally under a specific framework's row above (OpenAI Agents SDK,
Smolagents, LangGraph, LangGraph, CrewAI, CrewAI respectively), so keeping
the generic duplicate here only inflates counts without adding coverage.

| Pattern Type | Regex Pattern | Description | Source |
|-------------|---------------|--------------|--------|
| Delegate Call | `delegate_to_agent\s*\(`<br>`\.delegate\s*\(` | Direct delegation call | original |
| Sub-agent Dispatch | `sub_agent\.run\s*\(` | Manager routing to a sub-agent | original |
| A2A Invocation | `invoke_agent\s*\(` | Direct agent-to-agent invocation | migrated from `agent_calls.md` |
| Child Agent Invoke | `agent\.invoke_child\s*\(` | Invoking a child agent | migrated from `agent_calls.md` |
| Tool Agent Invoke | `tool\.invoke_agent` | Agent invoked as a tool | migrated from `agent_calls.md` |
| A2A Protocol | `A2A`<br>`agent-to-agent` | Protocol indicators — gate on co-occurrence with `A2AClient(`/`a2a-sdk` import to avoid matching unrelated prose | migrated from `agent_calls.md` |


---

## Confirmation Gating

As with framework detection in `pattern_scan`, several signals above (`.run(` inside a
delegation context, bare `Command(`, `.add_edge(`) are generic enough to produce false
positives on their own. A handoff should only be "confirmed" when:
1. A multi-agent constructor (≥ 2 agent/team/crew instances) is present in the same
   repository, **and**
2. At least one of the framework-specific or protocol-level patterns above appears
   between two distinct agent bindings.

This mirrors the creation-category gating already used for framework confirmation and
avoids counting single-agent graphs (e.g. a `LangGraph` state machine with no second
agent) as a handoff.