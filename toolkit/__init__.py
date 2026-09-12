"""
pattern_scan -- detects framework usage patterns in the RADAR repo
corpus, driven by the six vetted category markdown files at the
project root (agent_creation.md, agent_calls.md, rag_creation.md,
rag_writes.md, rag_reads.md, a2a_interaction.md).

Modules:
    pattern_index.py    -- builds data/patterns_verified.json from the .md files
    pattern_detector.py -- per-file detection (ast for Python, regex for JS/TS)
    incremental_json.py -- crash-safe, low-memory streaming JSON array writer
    util.py              -- timestamp helpers + run-metadata sidecar
    scan_local.py        -- scans repos already cloned to local disk
    scan_api.py           -- scans repos via the GitHub API, tarball-in-memory, no disk clone

Run everything from the PROJECT ROOT (the directory containing this
pattern_scan/ folder, plus your existing agent_creation.md etc. and,
for scan_api.py, github_fetcher.py):

    python -m pattern_scan.pattern_index .
    python -m pattern_scan.scan_local repos.json cloned_repos/
    python -m pattern_scan.scan_api repos.json
    python -m pattern_scan.tests.test_scan_api
"""
