# What This System Does — Full Resume + Simple Explanation

Two things in this document: (1) a complete recap of every piece, in plain language,
and (2) a short version you can use to explain this to someone else — your advisor,
a fellow student, anyone — without walking through the code.

---

## Before you start: you need a GitHub token

Every step that actually reads a repo's code (`github_fetcher.py`, `scan_api.py`)
talks to the GitHub API, and GitHub requires a personal access token for that — the
tool will refuse to run without one. Without a token you're limited to 60 requests
per hour, which isn't enough to scan a corpus of any real size.

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

Set this once per terminal session before running anything below. If you ever see
`"No GitHub token found"`, this is the fix.

---

## The one-paragraph version (use this to explain it out loud)

> RADAR needs to know, for each of the 516 repos in the corpus, things like "how many
> agents does this app have," "does it use a shared memory database," and "does it
> write untrusted data into that memory without checking it first." Instead of reading
> 516 repos by hand, I built a tool that downloads each repo's code, reads it the way
> a compiler would (not just searching for text), recognizes known agent-framework
> patterns (CrewAI, LangChain, AutoGen, etc.) — cross-checked against each file's
> actual import statements — and automatically fills in those answers. The patterns
> it looks for come from six reference documents I wrote by hand and verified against
> real repos; the tool itself just applies them at scale.

That's the whole system. Everything below is the detail behind that one paragraph.

---

## Part 1 — The six reference documents (the "what to look for" list)

Before any code runs, there are six files that define, in plain markdown tables,
every code pattern that means something:

| File | What it lists |
|---|---|
| `agent_creation.md` | Code that creates an agent (`Agent(...)`, `AssistantAgent(...)`, etc.) |
| `agent_calls.md` | Code that runs/invokes an already-created agent |
| `rag_creation.md` | Code that creates a memory/vector database (Chroma, Pinecone, etc.) |
| `rag_writes.md` | Code that writes/saves data into that memory |
| `rag_reads.md` | Code that reads/retrieves data from that memory |
| `a2a_interaction.md` | Code where one agent hands off a task to another agent |

These are the ground truth. If a pattern isn't in one of these six files, the tool
doesn't know to look for it. This is also why, when we found gaps earlier (missing
decorators, missing handoff patterns, and later a few patterns that turned out to be
*too* broad and caused false alarms — like `.compile(` matching plain `re.compile()`),
the fix was almost always to edit these tables — not the code.

---

## Part 2 — Turning those documents into something a program can use

**`pattern_index.py`** reads all six `.md` files and converts every backtick-quoted
pattern in their tables into one structured file: `patterns_verified.json`. This step
exists so that editing a `.md` file is the *only* thing you ever need to do to change
what the tool looks for — there's no second place to remember to update.

Think of this like turning a shopping list written in your own handwriting into a
barcode scanner's product database — same information, just in a form the next step
can search quickly.

**Run this once, and again any time you edit a `.md` file:**
```bash
python -m pattern_scan.pattern_index
```

---

## Part 3 — Getting a repo's code without downloading the whole repo

**`github_fetcher.py`** talks to GitHub's API directly. For each repo, it asks for
two things: basic info (stars, main branch name) and a compressed archive of the
entire codebase — all in 2 requests, not one request per file. This matters because
requesting one file at a time (which an earlier, simpler version of this tool did)
is slow and can make it look like the program has frozen when it's really just doing
hundreds of small requests one after another. This is also where the GitHub token
from the top of this document actually gets used.

---

## Part 4 — Narrowing down which files are even worth reading

Before any deep analysis, the tool works out which files could possibly matter: those
that import a tracked framework directly, plus those that import *another file* that
does (following the chain as far as it goes). Everything else is skipped, and the
count appears in the output as `files_scoped_out`. On a large repo this avoids
analysing thousands of files that have nothing to do with agents.

---

## Part 5 — Actually reading the code (the core of the whole system)

**`pattern_detector.py`** is where the real analysis happens. For every Python file
in a repo, it does roughly this:

1. **Reads the file like a compiler would, not like a text search.** This is the
   single most important design choice. A plain text search for `.add(` can't tell
   the difference between a database write and an unrelated dictionary operation.
   Reading the code's actual structure means the tool knows *which variable* a
   method is being called on, and *what kind of thing* that variable is.

2. **Recognizes "families" of code, not just exact matches.** If a project makes its
   own custom version of an agent class — like `class MyAgent(autogen.AssistantAgent)`
   — the tool understands that `MyAgent` is really just an AutoGen agent wearing a
   different name, because it inherited from the real one. This was a real gap we
   found by testing on an actual repo, where every agent was created exactly this way.

3. **Double-checks against the file's actual imports — per name, not per file.**
   Some frameworks share the exact same pattern (Agno and CrewAI both use a plain
   `Agent(...)` call). The tool checks where *that specific name* came from: if the
   file says `from crewai import Agent`, that settles it. And if a file imports Agno
   for one thing but separately defines its own unrelated `class Workflow`, that
   `Workflow()` is correctly **not** attributed to Agno — being imported somewhere in
   the file isn't enough, the name itself has to trace back. Renamed imports are
   handled too: `from chromadb import Client as MyDB` then `MyDB()` resolves back to
   its real identity before matching, so the rename doesn't hide it.

4. **Follows things across files.** If a memory database is created in `store.py` and
   written to in `ingest.py` (`from store import kb`, then `kb.add(...)`), that write
   is correctly counted. Same for agents created in one file and called in another —
   and that works even when the agent's display name differs from its variable name
   (`researcher = Crew(role="analyst")` is still findable as `researcher`, the name
   another file would actually import). It also holds when the pieces are spread
   across three files: a store defined in one, an agent linked to it in another
   (`Crew(memory=kb)`), and the write in a third still produces the right
   `rag_writers`/`rag_readers`. A first pass records what each file exports; a second
   pass uses that knowledge. Safety check built in: if the importing file *reassigns*
   that name to something else, attribution correctly stops.

5. **Tracks renaming within a file.** `worker = kb` followed by `worker.add(...)`
   still counts as a write to `kb`. Attributes on objects work too —
   `self.kb = Chroma()` then `self.kb.add(...)` is correctly connected.

6. **Only counts a tool as "usable" if an agent can actually use it.** Just having a
   web-scraping library somewhere in a repo doesn't mean any agent uses it. The tool
   specifically checks whether that tool was actually handed to an agent (via
   `tools=[...]`) before counting it.

7. **Also spots tool use that doesn't go through a framework.** Some repos build
   their own agent from scratch on top of the raw OpenAI/Anthropic SDKs. Those still
   have to hand their tools to the model through the SDK's own `tools=` parameter, so
   the tool detects that too — reported as `has_confirmed_llm_tool_calling`, separate
   from the framework-based `n_tools`.

8. **Does a rough "was this checked first?" test on memory writes.** When code saves
   something into memory, the tool traces back one step to see where that data came
   from. If it looks like it came from the internet, a user, or another tool — and no
   validation step happened nearby — it's flagged as a possible risk. This is a
   simple, one-step check, not a deep investigation — described more below.

---

## Part 6 — Adding up all the files into one answer per repo

**`scan_api.py`** takes every file's individual findings and combines them into one
final answer per repo — this is where the specific numbers your paper needs get
computed:

| Field | Plain meaning |
|---|---|
| `n_agents` | How many agents this repo creates |
| `n_tools` | How many tools are actually usable by an agent (not just present) |
| `has_rag` | Does this repo use any memory/vector database at all |
| `shared_across_agents` | Do two or more agents use the *same* memory database |
| `rag_writers` | Which agents can write into memory |
| `rag_readers` | Which agents can read from memory |
| `unsanitized_writes` | Was anything written to memory without being checked first |
| `has_confirmed_llm_tool_calling` | Does it hand tools to an LLM directly (custom, non-framework agents) |
| `llm_tool_names` | Those tool names, when they're written out literally in the code |

Alongside the summary, the output carries the evidence behind it:

| Field | What it holds |
|---|---|
| `findings_by_file` | Every individual thing found, grouped by file, each with the exact text that triggered it and its line number — plus that file's own imported frameworks, so you can see the attribution is consistent |
| `named_stores` | Every confirmed memory/vector database, with file and line |
| `unattributed_findings` | Things that look agent- or tool-related but couldn't be traced to any tracked framework (see below) |
| `file_imports` | Full import list, for every file importing at least one tracked framework |
| `files_scanned` / `files_scoped_out` | How much of the repo was actually analysed |

**Run it with:**
```bash
python -m pattern_scan.scan_api repos.json
```

---

## Part 7 — What about repos that don't use any tracked framework?

Some repos build a genuinely sophisticated agent entirely from scratch — no CrewAI,
no LangChain, just raw API calls (a real example in the corpus: NousResearch's
hermes-agent). For those, `n_agents` and `n_tools` correctly report 0, because there's
no tracked framework to detect. That's accurate, not a failure — but it would be
misleading on its own.

So the tool also records, separately and clearly labelled as unconfirmed:

- **`agent_marker`** — things that look like a hand-rolled agent (`class AIAgent`,
  `def run_agent(`, `agent = ...`)
- **`tool_use`** — custom tool-registration conventions (`register_tool(`,
  `tool_registry`, `get_tool_definitions(`)

These appear in `findings_by_file` with file and line, and are collected in
`unattributed_findings`. They never touch `n_agents` or `n_tools` — they're a "this
repo is worth a manual look" signal, not a count.

---

## Part 8 — Saving the results safely

**`incremental_json.py`** and **`util.py`** handle the boring-but-important plumbing:
results are written to disk one repo at a time (not all at once at the very end), so
if the program crashes halfway through scanning 500 repos, you don't lose the 300
you'd already finished. Re-running the exact same command automatically skips repos
already saved and picks up where it left off.

---

## The honest limitations — say these out loud too, they're not embarrassing

Being upfront about what this *doesn't* do is part of what makes the results
trustworthy, the same way the thesis already treats κ≈0.10 as a finding rather than
a flaw. Four things worth stating plainly if asked:

1. **Cross-file tracking works for direct imports, not every route.** If a store or
   agent is created in one file and imported by name into another, that's followed
   correctly. But if it's passed through a function's return value, stored on a class
   attribute in a different module, or re-exported through a chain of `__init__.py`
   files, it still won't be connected. Narrower than "one file at a time," but not zero.

2. **The "was this checked first?" test is a shortcut, not a deep investigation.**
   It looks one step back and checks for a few keywords — it can occasionally miss
   a real risk, or flag something safe as risky if an unrelated word happens to sit
   nearby in the code.

3. **Nothing is counted unless it can be traced to a tracked framework.** When a name
   can't be confirmed, the tool reports nothing rather than guessing. This was a
   deliberate choice (precision over recall), but it does mean genuinely agentic code
   built outside the 39 tracked frameworks is invisible to the headline numbers — see
   Part 7 for how that's surfaced separately.

4. **JavaScript/TypeScript is much weaker than Python.** All the structural analysis
   above — imports, cross-file tracking, renaming, attribution — is Python-only.
   JS/TS files fall back to plain text matching.

None of these break the tool — they define its precision, the same way any static
analysis tool has edges. They're documented directly in the code, not hidden.

---

## The 30-second version, if someone stops you in a hallway

> "I built a tool that reads agentic-AI GitHub repos the way a compiler would, spots
> known patterns from frameworks like LangChain and CrewAI — double-checked against
> each file's actual imports, and followed across files — and automatically figures
> out things like how many agents a repo has and whether it's vulnerable to memory
> poisoning — instead of me reading 500 repos by hand."

That's genuinely the whole thing.