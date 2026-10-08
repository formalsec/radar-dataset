# Toolkit

The toolkit statically analyzes repository source code for agent, tool, retrieval-augmented generation (RAG), memory, and agent-to-agent interaction patterns. It consumes repository manifests from `dataset/`, downloads repositories through the GitHub API, scans relevant source files, and writes per-repository findings and corpus summaries.

Run toolkit commands from the repository root so Python package imports and default output paths resolve correctly.

## Source files

### `src/scan_api.py`

Main scan command and API-based orchestration layer. It reads a repository manifest, downloads repository tarballs, excludes generated/test/example directories, analyzes relevant source files, aggregates repository-level RADAR fields, and writes incremental JSON results plus a metadata sidecar.

It can resume an interrupted output file and supports options for custom patterns, repository limits, maximum file size, garbage-collection frequency, and GitHub authentication.

Each repository is analyzed in two passes. The first finds every store and agent; the second re-analyzes the files that import one of them by name from another file, so a write, read, call, or `Agent(memory=kb)` link is attributed to the specific store or agent even when it was created elsewhere.

```bash
python -m toolkit.src.scan_api dataset/all_repos.json --limit 50
```

### `src/pattern_detector.py`

Core detection engine. It applies the indexed patterns to Python, JavaScript, and TypeScript source, records file/line evidence, identifies agents, tools, stores, reads, writes, and framework attribution, and computes the structured data used by `radar_summary`.

Python is parsed with `ast` and JavaScript/TypeScript with tree-sitter (`src/js_ast.py`). In both languages a call counts for exactly one framework: creations are confirmed by the import the name comes from, and writes, reads, and agent calls take the framework of the variable they are made on, so a shared verb such as `.add(` is not counted once per framework that lists it. Writes get a coarse one-hop taint check on their first argument.

Detection is heuristic. Cross-file resolution follows one direct named import; stores passed through function parameters, return values, re-exports, default exports, or tsconfig path aliases are not linked.

### `src/pattern_index.py`

Builds the machine-readable pattern index at `src/data/patterns_verified.json` from the Markdown definitions under `src/patterns/`.

```bash
python -m toolkit.src.pattern_index
```

### `src/github_fetcher.py`

GitHub API client used to retrieve repository metadata, trees, and source archives/content. It handles authentication, caching, and request/retry behavior needed by the scanner.

### `src/incremental_json.py`

Crash-safe streaming JSON-array writer and loader helpers. It lets scans save records as they complete and repair/load an output file that ends with a partial record after interruption.

### `src/util.py`

Shared scanning utilities, including UTC timestamps, timestamped output paths, JSON helpers, and run metadata used by result sidecars.

### `src/aggregate.py`

Reads a scan result file and produces human-readable rankings and corpus totals. It writes a repository-level CSV and a totals JSON. This is the counting step of the pipeline: agents, tools, and skills per repository.

```bash
python -m toolkit.src.aggregate toolkit/output/scan_results/<scan-file>.json
```

| Option | Description |
|---|---|
| `--repos FILE` | Count only the repositories listed in `FILE`, i.e. the ones that survived the earlier filters. Accepts a text file of URLs, a JSON list, a manifest such as `dataset/all_repos.json`, or a classifier output, of which only the `app` entries are kept |
| `--require-instantiation` | Count only repositories whose code calls a tracked framework (see `framework_usage` below) |
| `--csv PATH`, `--totals-json PATH` | Output paths; default to timestamped files under `output/aggregate_results/` |
| `--top N` | Number of repositories printed to the terminal |

### `src/__init__.py`

Package documentation describing the detector, pattern definitions, incremental writer, and supported module entry points.

## Pattern definitions

The Markdown files in `src/patterns/` are the human-readable detection rules:

- `agent_creation.md`: agent construction and registration.
- `agent_calls.md`: agent invocation and calls.
- `a2a_interaction.md`: communication or handoffs between agents.
- `rag_creation.md`: creation of indexes, vector stores, and other RAG resources.
- `rag_reads.md`: retrieval/query/read operations.
- `rag_writes.md`: writes to documents, indexes, and stores.
- `unsanitized_writes.md`: writes that lack the expected validation or sanitization.
- `tool_definition.md`: tool declaration and registration markers.

`src/data/patterns_verified.json` is the indexed/generated form consumed during scanning. When Markdown definitions change, rebuild this file before running a scan.

## Output directories

### `output/scan_results/`

Contains timestamped scan snapshots and their metadata sidecars. Scan JSON files are arrays of repository records. A scanned record typically includes repository identity, declared frameworks, status/error, scan time, file counts, `radar_summary`, detailed `findings`, and framework comparison data.

Important `radar_summary` fields include agent counts and evidence, tool counts and evidence, skill counts, confirmed tool definitions, RAG presence, shared stores, RAG readers/writers, and unsanitized writes.

Stores are reported per instance:

- `n_stores` and `store_evidence`: one entry per store creation, identified as `file:line`, with its framework, `n_writes`, `n_reads`, the agents it is linked to, and a `role` (`read_write`, `write_only`, `read_only`, `unused`). A store that is written but never read is a log or sink rather than retrieval memory.
- `store_frameworks`, `rag_write_frameworks`, `rag_read_frameworks`: the frameworks of the stores created, written, and read.
- `n_stores_written`, `n_stores_read`: how many distinct stores are written and read.
- `write` and `read` findings carry a `store` field naming the instance they target.

Each record also has `framework_usage`, a stricter version of the crawler's dependency filter:

- `instantiated`: frameworks with at least one call, decorator, or subclass in the scanned code whose name traces back to an import of the framework's package (`call_counts` has the numbers).
- `imported_only`: imported somewhere but never called.
- `declared_only`: found by the crawler in the dependency manifest but not imported in the scanned code.
- `passes_instantiation_filter`: true when `instantiated` is not empty.

Test, example, and vendored directories are never scanned, so these describe application code only.

Files ending in `.meta.json` contain provenance and progress information such as input and pattern paths, start/end times, sessions, and scanned/failed/skipped counts.

### `output/aggregate_results/`

Contains summaries created by `aggregate.py`:

- `app_corpus_totals*.json`: corpus-wide totals for scanned/failed repositories, agents, tools, skills, stores, RAG repositories, and related counts, plus `distributions` (for each count, how many repositories have each value), ready for histograms.
- `out*.csv`: one row per counted repository with `repo_name`, `repo_url`, `stars`, `n_agents`, `n_custom_agents`, `n_tools`, `n_skills`, `n_tool_definition_markers`, `has_rag`, `n_stores`, and `passes_instantiation_filter`. This is the file to build per-repository charts from.

## Typical workflow

1. Build or update the pattern index with `python -m toolkit.src.pattern_index`.
2. Scan a manifest from `dataset/` with `python -m toolkit.src.scan_api ...`.
3. Inspect the JSON findings and its `.meta.json` sidecar under `output/scan_results/`.
4. Aggregate the scan with `python -m toolkit.src.aggregate ...` to create CSV and totals JSON under `output/aggregate_results/`. Pass `--repos` with the list of repositories that survived the filters to count only those.

The scanner deliberately reports structural evidence rather than confirmed runtime exploits. Results should therefore be interpreted alongside the detector's limitations and the evidence locations in each finding.