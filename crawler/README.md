# Crawler

The crawler discovers GitHub repositories that may contain agentic applications. It searches configurable query groups, filters repositories by language composition and framework dependencies, stores progress incrementally, and optionally classifies repositories as applications or frameworks with language models.

Run crawler commands from `crawler/src/` unless a command says otherwise. The crawler writes persistent manifests to `../dataset/` and generated run artifacts to `crawler/output/`.

## Source files

### `src/main.py`

Command-line entry point for repository discovery. It validates the query group, obtains a GitHub token from `--token` or `GITHUB_TOKEN`, supports cache reset, and starts `RepoCrawler` with the requested target count.

Typical usage:

```bash
cd crawler/src
python main.py --group comprehensive --target 3000
```

Use `python main.py --list-groups` to list available groups.

### `src/config.py`

Central configuration for paths, language requirements, GitHub pagination, target defaults, query groups, and dataset filenames. The current language filter requires Python, JavaScript, or TypeScript to make up at least 70% of a repository's language composition.

### `src/crawler.py`

Implements the GitHub search and filtering workflow. It queries repositories, obtains repository metadata, applies language and framework checks, and writes passed and rejected URLs to the run output while updating the persistent storage manager.

### `src/frameworks.py`

Loads framework definitions and provides the matching logic used to determine whether a repository declares or uses an agent-related framework or package.

### `src/framework_list.md`

Human-maintained list of framework and package names used by the framework detector. Update this file when the supported framework allow-list changes.

### `src/storage.py`

Provides incremental persistence and duplicate prevention. It loads the dataset manifests, tracks passed repositories and searched/seen URLs, batches writes, exposes crawl statistics, and implements the complete cache reset used by `--reset-cache`.

### `src/utils.py`

Shared GitHub and repository helpers, including HTTP sessions and retries, API/file/tree access, dependency-file parsing, and language-percentage calculations.

### `src/gpt.py`

Client and progress helpers for GPT-based classification. It handles calls to an OpenAI-compatible model and presents classification progress.

### `src/repoClassifier.py`

Repository classification workflow based on repository metadata, README/content inspection, and local heuristics. It is used to distinguish likely applications from frameworks or other non-application repositories.

### `src/repoClassifierGPT.py`

LLM-backed repository classifier. It supports model selection, input/output files, limits, resume/checkpoint behavior, retries, interruption handling, and classification reasoning/error fields.

### `src/venn_packages,py`

Generates comparison inputs and visualizations from classifier results. The comma in the filename is part of the current filename; commands must reference the literal path, or the file should be renamed to the conventional `venn_packages.py` before using a conventional module command.

## Output directories

### `output/crawl_results_<timestamp>/`

Each timestamped directory represents one crawler run. Common files are:

- `all_searched.txt`: every candidate URL considered by that run.
- `passed_urls.txt`: repositories that passed the crawler filters.
- `passed_urls_resume.txt`: an older run's resume/checkpoint URL list.
- `failed_language.txt`: repositories rejected by the language-composition filter.
- `failed_framework.txt`: repositories rejected because no accepted framework/package was found.
- `run_<timestamp>.json`: structured run snapshot, when produced. It contains run metadata and repository records.

The text files are convenient for inspection and resuming; the JSON snapshot is better for structured processing. Empty or partial files can occur when a run is interrupted early.

### `output/llm_classifications/`

Stores classifier results for the same or overlapping repository URL sets. The JSON files contain model and timestamp metadata, totals, success/failure counts, classification counts, and per-URL classification, reasoning, and processing/error information. Existing files include results from DeepSeek-R1, GPT, and Llama 3.1.

### `output/venn_diagram/`

Stores classifier-overlap artifacts. `venn_input.csv` contains Boolean membership columns for each classifier, while `venn_applications.png` is the generated visualization of agreement and disagreement between classifiers.

## Persistent files outside `crawler/`

The crawler uses the files in `dataset/` as persistent state:

- `all_repos.json`: repositories that passed the filters.
- `searched_urls.json`: URLs returned or processed from GitHub searches.
- `seen_repos.json`: URL/name cache used for duplicate prevention.

See `dataset/README.md` for their schemas and lifecycle.

## Query groups

Query groups are defined in `src/config.py`. They cover broad agent terms (`agent_frameworks`), named frameworks (`specific_frameworks`), multi-agent terms (`multi_agent`), a combined group (`comprehensive`), and higher-value searches (`high_value`).

The crawler requires a GitHub token for practical repository-scale runs. Supply it with `--token` or set `GITHUB_TOKEN`; unauthenticated GitHub API access is heavily rate-limited.