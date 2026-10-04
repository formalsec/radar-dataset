# Dataset

This directory contains the repository manifests and persistent state used by the crawler. The files are JSON or JSONL artifacts rather than source code. The crawler reads and updates several of them incrementally, so they should be treated as part of the crawl state.

## Files

### `all_repos.json`

The main catalog of repositories that passed the crawler filters. It has two top-level fields:

- `metadata`: dataset size, last update time, and the minimum language percentage used by the crawler.
- `repos`: repository records containing the GitHub URL and name, stars, language percentages, detected frameworks, and related repository metadata.

This file is also a typical input manifest for the static-analysis toolkit.

### `repos_test.json`

A small repository manifest for testing or trial scans. It follows the same general `{ "metadata": ..., "repos": [...] }` structure as `all_repos.json`, but is intended for quick runs rather than the full corpus.

### `repo_metadata.jsonl`

A line-oriented metadata export with one JSON object per repository. Records include GitHub metadata such as stars, forks, watchers, repository size, file count, language byte counts and percentages, primary language, topics, license, branch, timestamps, and repository flags. Failed metadata requests may appear as records containing the URL, error, and processing timestamp instead of the full metadata fields.

JSONL is useful for streaming or inspecting records without loading the complete dataset into memory.

### `searched_urls.json`

The crawler's search-progress state. It stores the URLs returned by GitHub searches, a `total_searched` count, and `last_updated`. It prevents the same search results from being processed repeatedly across runs.

### `seen_repos.json`

The crawler's duplicate-detection cache. It stores repository URLs and names encountered so far, together with `total_seen` and `last_updated`. It includes repositories encountered during crawling, not only repositories that passed all filters.

## Data flow

1. The crawler searches GitHub and records search progress in `searched_urls.json`.
2. Encountered repository URLs and names are recorded in `seen_repos.json`.
3. Repositories passing the language and framework filters are appended to `all_repos.json`.
4. A manifest such as `all_repos.json` or `repos_test.json` can be passed to `toolkit/src/scan_api.py` for static analysis.

The crawler writes these files in batches so an interrupted run can usually resume from the saved state. Use the crawler's `--reset-cache` option only when a completely fresh crawl is intended; it removes the three state files above.
