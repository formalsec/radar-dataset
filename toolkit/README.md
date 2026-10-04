# Toolkit

The toolkit statically analyzes repository source code for agent, tool, retrieval-augmented generation (RAG), memory, and agent-to-agent interaction patterns. It consumes repository manifests from `dataset/`, downloads repositories through the GitHub API, scans relevant source files, and writes per-repository findings and corpus summaries.

Run toolkit commands from the repository root so Python package imports and default output paths resolve correctly.

## Source files

### `src/scan_api.py`

Main scan command and API-based orchestration layer. It reads a repository manifest, downloads repository tarballs, excludes generated/test/example directories, analyzes relevant source files, aggregates repository-level RADAR fields, and writes incremental JSON results plus a metadata sidecar.

It can resume an interrupted output file and supports options for custom patterns, repository limits, maximum file size, garbage-collection frequency, and GitHub authentication.

```bash
python -m toolkit.src.scan_api dataset/all_repos.json --limit 50
```

### `src/pattern_detector.py`

Core detection engine. It applies the indexed patterns to Python, JavaScript, and TypeScript source, records file/line evidence, identifies agents, tools, stores, reads, writes, and framework attribution, and computes the structured data used by `radar_summary`.

Detection is heuristic. Some cross-file store/agent links and aliased imports are intentionally not resolved.

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

Reads a scan result file and produces human-readable rankings and corpus totals. It can also write a repository-level CSV and totals JSON.

```bash
python -m toolkit.src.aggregate toolkit/output/scan_results/<scan-file>.json
```

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

Important `radar_summary` fields include agent counts and evidence, tool counts and evidence, confirmed tool definitions, RAG presence, shared stores, RAG readers/writers, and unsanitized writes.

Files ending in `.meta.json` contain provenance and progress information such as input and pattern paths, start/end times, sessions, and scanned/failed/skipped counts.

### `output/aggregate_results/`

Contains summaries created by `aggregate.py`:

- `app_corpus_totals*.json`: corpus-wide totals for scanned/failed repositories, agents, tools, RAG repositories, and related counts.
- `out*.csv`: one row per scanned repository with fields such as repository name, stars, agent counts, tool counts, unconfirmed tool-definition markers, and `has_rag`.

## Typical workflow

1. Build or update the pattern index with `python -m toolkit.src.pattern_index`.
2. Scan a manifest from `dataset/` with `python -m toolkit.src.scan_api ...`.
3. Inspect the JSON findings and its `.meta.json` sidecar under `output/scan_results/`.
4. Aggregate the scan with `python -m toolkit.src.aggregate ...` to create CSV and totals JSON under `output/aggregate_results/`.

The scanner deliberately reports structural evidence rather than confirmed runtime exploits. Results should therefore be interpreted alongside the detector's limitations and the evidence locations in each finding.