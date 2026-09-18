"""
pattern_scan -- detects framework usage patterns in the RADAR repo
corpus, driven by the six vetted category markdown files at the
project root (agent_creation.md, agent_calls.md, rag_creation.md,
rag_writes.md, rag_reads.md, a2a_interaction.md).

Modules:
    pattern_index.py    -- builds src/data/patterns_verified.json from the .md files
    pattern_detector.py -- per-file detection (ast for Python, regex for JS/TS)
    incremental_json.py -- crash-safe, low-memory streaming JSON array writer
    util.py              -- timestamp helpers + run-metadata sidecar
    scan_local.py        -- scans repos already cloned to local disk
    scan_api.py           -- scans repos via the GitHub API, tarball-in-memory, no disk clone

Run toolkit modules from the PROJECT ROOT:

    python -m toolkit.src.pattern_index
    python -m toolkit.src.scan_api dataset/all_repos.json
"""
