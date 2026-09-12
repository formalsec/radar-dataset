# How Each `radar_summary` Field Is Computed

Reference doc for the seven fields in every repo's `radar_summary` block. Each one
traces back to a specific mechanism in `pattern_detector.py` (per-file detection) and
`scan_api.py` (repo-level aggregation). Nothing here is a raw pattern-match count —
every field routes through single-attribution resolution, which is what avoids the
duplicate-counting problem (the same call incrementing multiple frameworks) fixed
earlier in this project.

---

## `n_agents` and `agent_names`

**Source:** `named_agents`, built per-file in `_reduce_python()`.

For every `Call` node in the AST, the tool checks whether the call text matches an
`agent_creation`-category pattern — either directly (`Agent(`) or via subclass
resolution (`SolidAssistantAgent(` recognized because it inherits from
`autogen.AssistantAgent`). Each match becomes a `named_agents` entry with a `name`
taken from a `name=`/`role=`/`id=` keyword argument if present, otherwise falling
back to the variable the result was assigned to (or `None` if neither exists, e.g.
a bare `return Agent(...)`).

Repo-level aggregation:
```python
unique_agent_names = sorted({a["name"] for a in agent_instances if a["name"]})
n_agents = len(unique_agent_names) or sum(1 for a in agent_instances if not a["name"])
```
`agent_names` is the deduplicated list; `n_agents` is that count, falling back to
counting anonymous creation sites only if nothing had a name at all.

---

## `n_tools`

**Source:** `tools_bound`, extracted from each agent-creation call's own arguments.

At the same creation point, `_extract_tools_bound()` looks for a `tools=[...]`
keyword argument and pulls out every `Name` inside that list. This is
**attribution-gated**: a tool only counts if it's actually passed into an agent's
`tools=` argument, not merely present somewhere in the repo.
```python
tools_bound_all = set()
for a in agent_instances:
    tools_bound_all.update(a.get("tools_bound", []))
n_tools = len(tools_bound_all)
```
Deduplicated union across every agent in the repo.

---

## `has_rag`

**Source:** `named_stores` — same mechanism as `named_agents`, checked against the
`rag_creation` category instead (`Chroma(`, `VectorStoreIndex.from_documents(`, etc.).
```python
has_rag = len(store_instances) > 0
```
True the moment any vectorstore/memory creation is found anywhere in the repo.

---

## `shared_across_agents`

**Source:** `store_agent_links` — formed when an agent's constructor call has a
keyword argument (`memory=`, `vector_store=`, `retriever=`, `knowledge=`, `store=`)
whose value is a `Name` matching an already-known store variable, e.g.
`Crew(memory=kb)`.

Repo-level grouping:
```python
store_to_agents = {}
for link in store_agent_links:
    store_to_agents.setdefault(link["store"], set()).add(link["agent"])
shared_across_agents = any(len(agents) >= 2 for agents in store_to_agents.values())
```
True if any single store variable was linked to two or more *different* agent names.

**Known limitation baked into this field:** the link only forms if the store is
passed directly as a constructor keyword — it doesn't catch a store merely
*referenced* inside a method body elsewhere, and it's single-file only (a store
created in one file and used by an agent in another won't be linked).

---

## `rag_writers` and `rag_readers`

**Source:** `write_sites`/`read_sites` (every call matching a `rag_writes`/`rag_reads`
pattern, e.g. `.add(`, `.query(`) cross-referenced against `store_to_agents` (the
same grouping used for `shared_across_agents`).
```python
rag_writers = sorted({
    agent for w in write_sites
    for agent in store_to_agents.get(w.get("variable"), [])
})
```
For each write, look up which store variable it was called on, then look up which
agent(s) that store is linked to. A write on a store never linked to any agent
contributes nothing here — it still appears in `findings` with `framework` filled
in, just with no agent attribution.

---

## `unsanitized_writes`

**Source:** the one-hop taint check, run on every `write_sites` entry.

For each write call, `_is_write_argument_tainted()` takes the call's argument
(unwrapping one level of list, since `.add(documents=[x])` is the dominant shape),
traces `x` back to its most recent assignment **in the same function**, and checks
that source text against a fixed marker list (`fetch`, `request`, `response`,
`.text`, `retrieved`, etc.). If tainted, `_has_sanitizer_nearby()` scans the 5 lines
above for a sanitizer keyword (`validate`, `sanitize`, `schema`, etc.).
```python
"unsanitized": tainted and not sanitized_nearby
```
```python
unsanitized_writes = any(w["unsanitized"] for w in write_sites)
```
True if *any single write* in the repo was tainted with no sanitizer nearby.

**Two known false-negative risks:**
- The sanitizer check can be fooled by an unrelated word sitting nearby (e.g. the
  string `"clean data"` in a different line) — confirmed during testing.
- The taint check only looks back **one** assignment, not further — this is a
  one-hop heuristic, not real dataflow propagation.

---

## The common thread

All seven fields ultimately reduce to two structures:
- `named_agents` / `named_stores` — resolve *what* was created
- `write_sites` / `read_sites` / `store_agent_links` — resolve *how things relate*

Nothing here is a bare pattern-match count. Every field is downstream of the
single-attribution logic built specifically to prevent one call site from
incrementing multiple frameworks at once.