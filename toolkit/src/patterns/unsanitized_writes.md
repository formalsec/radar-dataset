# How `unsanitized_writes` Detection Works

Reference doc for the exact mechanism behind `radar_summary`'s `unsanitized_writes`
field, and the write-level `unsanitized`/`tainted` flags inside `findings`. This is
a **one-hop heuristic**, not real dataflow/taint tracking — that's a deliberate
simplicity choice, documented here so the precision trade-off is visible rather
than assumed.

---

## The three-step check, per write call

For every call matching a `rag_writes`-category pattern (`.add(`, `.upsert(`, etc.):

**Step 1 — Get the argument.** Take the call's first argument. If it's a list or
tuple (the dominant real shape — `.add(documents=[x])`), unwrap one level and check
each element inside it.

**Step 2 — Trace back one assignment.** If the argument is a bare variable (e.g.
`raw` in `kb.add(documents=[raw])`), look up the *most recent* assignment to that
name **in the same function** (not across functions, not across files) and use
*that* expression's text instead. If the variable was never assigned in-scope, its
own name is used as-is.

**Step 3 — Check that text against two keyword lists.**

### Taint source markers — is this "tainted" at all?
```python
TAINT_SOURCE_MARKERS = [
    "request", "response", ".text", ".content", "http", "fetch",
    "tool_output", "tool_result", "retrieved", "retrieval",
    "user_input", "external", "web_content",
]
```
If any of these appear (as a plain substring, case-insensitive) in the traced-back
source text, the write is marked `tainted: True`. This is why `raw = fetch_web_page(url)`
followed by `kb.add(documents=[raw])` gets flagged — `"fetch"` appears in
`fetch_web_page(url)`.

### Sanitizer keywords — was it checked before the write?
```python
SANITIZER_KEYWORDS = ["validate", "sanitize", "clean", "escape", "schema", "pydantic"]
```
Only checked if the write was already tainted. Looks at the **5 lines immediately
above** the write call (plain text search, not structural) for any of these words.
If found, `sanitizer_nearby: True` and the write is considered handled.

## The final flag
```python
unsanitized = tainted and not sanitizer_nearby
```
`radar_summary.unsanitized_writes` is `True` if **any single write** in the repo
comes back `unsanitized: True`.

---

## Known limitations — confirmed by testing, not theoretical

**One hop only.** If untrusted data passes through two assignments before reaching
a write (`a = fetch(...)`; `b = a`; `kb.add(documents=[b])`), the trace stops after
one step and may miss the connection, depending on where exactly the chain breaks.

**Same-function only.** If the tainted value is assigned in one function and the
write happens in another (even if one calls the other), the trace won't cross that
boundary.

**Sanitizer keyword false negatives — reproduced concretely.** An unrelated string
containing a sanitizer word can wrongly suppress a real risk. Confirmed case:
```python
kb.upsert(documents=["clean data"])   # unrelated call, one line above
raw = fetch_web_page(url)
kb.add(documents=[raw])               # genuinely tainted...
```
The word `"clean"` inside `"clean data"` — from the *previous, unrelated* call —
falls within the 5-line lookback window and incorrectly marks the second,
genuinely risky write as sanitized.

**Substring matching, not semantic understanding.** Both keyword lists match plain
substrings. A variable named `external_config` (a local settings object, nothing to
do with external data) would trigger a taint match on `"external"` alone, since the
check has no concept of what the variable actually represents.

**No sanitizer function is verified to actually validate anything.** If a function
happens to be named `validate_input` but performs no real checks, its mere presence
nearby is still enough to suppress the `unsanitized` flag — the check confirms a
*word*, not a *behavior*.

---

## Why this design, despite the limitations

This mirrors the project's broader simplicity choice: real interprocedural taint
tracking (crossing functions, files, multiple hops) is what tools like Semgrep's
taint mode or CodeQL are built for, and reproducing that from scratch was
deliberately out of scope. The one-hop, same-function, keyword-based version here
is meant to catch the common, obvious case — an external fetch flowing directly
into a write with nothing in between — while being simple enough for a future
student to read start to finish and understand exactly what it does and doesn't
catch.