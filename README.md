# RADAR: a corpus and static-analysis toolkit for open-source agentic applications

RADAR is a research project that studies how real-world agentic applications are built and how often they expose security-relevant architectural patterns. The repository combines:

- a curated corpus of GitHub repositories that are plausibly agentic applications,
- a crawler and filtering pipeline to identify those repositories at scale,
- a static-analysis toolkit that detects agent architecture, memory/RAG usage, tool access, and security-critical patterns.

This project focuses on measuring the prevalence of agentic risks and defenses in real open-source software.

## Research goal

The project addresses a simple but important empirical question: in the real world, how often do agentic applications include risky patterns such as untrusted data flow, shared memory poisoning surfaces, tool execution, and external input handling, and how often do they include corresponding defensive mechanisms?

The analysis is intentionally structural rather than exploit-driven: it flags design patterns and exposures in source code, not confirmed runtime exploitation.

## Repository layout

- crawler/: repository discovery, language filtering, framework detection, and README-based application/framework classification
- crawler/src/: crawler and LLM-based classifiers for separating applications from frameworks
- crawler/output/: crawler runs, classification results, and Venn-diagram inputs/outputs
- toolkit/: static-analysis tooling for scanning repository code
- toolkit/src/patterns/: human-authored pattern definitions used to detect agentic constructs and risky data flows
- toolkit/src/: scanner and result aggregation code
- toolkit/output/: scanner results and metadata sidecars
- results and JSON artifacts: examples of intermediate and final outputs

## Dataset and methodology

The final corpus is a set of agentic applications collected from GitHub and selected to reflect diversity across frameworks, domains, and autonomy levels.

The acquisition pipeline is:

1. GitHub search for repositories mentioning agent terms and primarily implemented in Python, JavaScript, or TypeScript.
2. Language composition filter: keep repositories where at least 70% of code is in those languages.
3. Framework dependency filter: keep repositories that import or declare a dependency on at least one agent-development framework or protocol in the allow-list.
4. Application vs. framework classification (S1) using multiple LLM judges plus manual review for ambiguous cases.
5. Agentic application filter (S2): a six-label classification of the S1 applications that keeps the agentic ones.
6. Repository download and static analysis of the surviving applications.

The resulting dataset contains repository-level metadata and security-relevant architectural findings.

## What the toolkit measures

The static-analysis layer extracts metadata fields that characterize the security posture of each application, grouped into four categories:

- Structure: number of agents, tool counts, retrieval use, handoffs between agents
- Memory and retrieval: shared memory, RAG writers/readers, unsanitized writes, retrieved content influencing action
- Tool access and privilege: tool categories, authenticated tools, execution-capable tools, sensitive data access
- Input and output handling: external input sources, untrusted data channels, output destinations, output guardrails

The detector is built on AST-based pattern matching, lightweight import resolution, and coarse taint checks. It is designed to detect code patterns that correspond to the paper's risk and defense taxonomy rather than arbitrary string matches.

## Typical risk / defense signals

The project tracks signals corresponding to categories such as:

- risk-bearing architecture: broad untrusted input surfaces, shared memory, agent handoffs, execution-capable tools
- malicious or corrupted data flow: retrieval and external content influencing prompts or actions
- unsafe writes: information stored without validation or schema enforcement
- confidentiality and integrity risks: sensitive data exposure or privileged operations without mediation
- defenses: output guardrails, alerting/monitoring, privilege separation, validation gates, human approval, access control, and tool hardening

## Quick start

### Prerequisites

Create a virtual environment and install the minimal dependencies used by the crawler and scanner:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install requests
```

Set a GitHub token for API access:

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

GitHub unauthenticated access is limited to ~60 requests per hour, which is not sufficient for repository-scale analysis.

### 1) Crawl candidate repositories

The crawler configuration lives in crawler/src/config.py. It defines the language scopes, search queries, and repository-selection thresholds.

```bash
python main.py --group <group_name> --target <number> [--token TOKEN]
```

| Flag | Description |
|---|---|
| `--group` | **Required.** Query group to run (see below for options) |
| `--target` | Number of repos to find (default: see `DEFAULT_TARGET_REPOS` in `config.py`) |
| `--token` | GitHub API token (optional if `GITHUB_TOKEN` env var is set) |
| `--list-groups` | List available query groups and exit |

### Query groups

| Group | Description |
|---|---|
| `agent_frameworks` | Broad, generic agent-related terms (e.g. "agent", "autonomous agent") |
| `specific_frameworks` | Named, well-known agent frameworks (e.g. LangChain, AutoGen, CrewAI) |
| `multi_agent` | Terms specific to multi-agent systems and orchestration |
| `comprehensive` | Union of the broad, specific, and multi-agent terms above |

Run `python main.py --list-groups` to see the exact query strings in each group.

The crawler writes intermediate results into timestamped directories under crawler/output/crawl_results_*/ and retains the repositories that pass both the language and framework filters. Its persistent dataset state is stored under dataset/.

### 2) Classify repositories as applications vs. frameworks (S1)

The classification step is handled under crawler/src/llm_screening/ and writes each judge's classifications to crawler/output/llm_screening/s1_app_vs_framework/judges/ by default.

For Ollama-based classification:

```bash
ollama serve
python crawler/src/llm_screening/s1_classify.py \
  --input urls_to_classify.txt \
  --output crawler/output/llm_screening/s1_app_vs_framework/judges/llama3.3.json \
  --model llama3.3
```

For GPT-based classification:

```bash
python crawler/src/llm_screening/s1_classify_gpt.py \
  --input urls_to_classify.txt \
  --output crawler/output/llm_screening/s1_app_vs_framework/judges/gpt-5.4-mini.json \
  --model gpt-5.4-mini
```

The scripts accept `--limit` and `--resume` options and write aggregate counts together with model reasoning and errors.

To merge the judges with the manual review (crawler/output/llm_screening/s1_app_vs_framework/manual_review.xlsx) into the S1 dataset:

```bash
python crawler/src/llm_screening/merge_judges.py
```

A repository is kept when all judges label it `app`, or, when the judges disagree, when the manual review does. The manual review is only consulted when the judges disagree. This writes dataset/s1_apps.json (same format as dataset/all_repos.json) and dataset/s1_apps.xlsx (summary and per-repository labels).

### 3) Keep the agentic applications (S2)

S2 classifies each S1 application into one of six labels (Agentic Applications, Non-Agentic Software, Curated Lists, Courses & Playbooks, External-Agent Wrappers & Interfaces, Tools & Skills for External Agents, Templates, Models & Benchmarks) or Needs Review. It reads dataset/s1_apps.json and writes each judge to crawler/output/llm_screening/s2_app_cleanup/judges/<model>.json by default.

```bash
export GITHUB_TOKEN="your-token"
python crawler/src/llm_screening/s2_classify.py --model claude-sonnet-5-5
python crawler/src/llm_screening/s2_classify.py --provider openai --model gpt-5.5
```

To merge the S2 judges (every file in its judges/ folder, plus crawler/output/llm_screening/s2_app_cleanup/manual_review.xlsx when it exists) into dataset/s2_agentic_apps.json and .xlsx:

```bash
python crawler/src/llm_screening/merge_judges.py --step s2
```

### 4) Run the static scanner

The scanner takes a JSON manifest of repositories (for example, a corpus or filtered subset) and analyzes the referenced repository source code to emit structured findings per repository.

```bash
python -m toolkit.src.scan_api <repos_json> [output_file] [--patterns FILE] [--token TOKEN] [--max-file-size BYTES] [--limit N] [--gc-every N]
```

| Argument | Description |
|---|---|
| `<repos_json>` | **Required.** Path to the JSON manifest listing repositories to scan |
| `[output_file]` | Optional path for the scanner output; defaults to a timestamped file under `toolkit/output/scan_results/` |
| `--patterns FILE` | Custom pattern-definition file to use instead of the default definitions |
| `--token TOKEN` | GitHub API token for authenticated metadata lookups when needed |
| `--max-file-size BYTES` | Skip files larger than this size during analysis |
| `--limit N` | Process only the first `N` repositories in the manifest |
| `--gc-every N` | Trigger garbage collection every `N` repositories to reduce memory pressure |

Example:

```bash
python -m toolkit.src.scan_api dataset/all_repos.json --limit 50
```

## Output format

The scanner produces records with repository summaries and evidence. Typical fields include:

- repo_name, repo_url, declared_frameworks
- status, error, scanned_at
- radar_summary with values such as n_agents, n_tools, has_rag, shared_across_agents, rag_writers, rag_readers, unsanitized_writes
- findings_by_file and other traceable evidence for each repository

## Pattern definitions

The rules for matching relevant code patterns are kept in markdown files under toolkit/src/patterns/:

- agent_creation.md
- agent_calls.md
- rag_creation.md
- rag_reads.md
- rag_writes.md
- unsanitized_writes.md
- a2a_interaction.md

The scanner reads these definitions and compiles them into a pattern index used at analysis time.

## Limitations

This project has several important limitations that should be kept in mind when interpreting results:

- The corpus is restricted to GitHub repositories and to Python/JavaScript/TypeScript projects.
- The crawl is biased toward popular and better-known repositories.
- Framework-vs-application classification is done from README text and can be noisy for sparse or misleading documentation.
- The static analysis is heuristic and best interpreted as structural evidence rather than exploit confirmation.
- Some cross-file and alias-resolution cases may be missed, especially in large or highly indirect codebases.
- The dataset is a snapshot in time and will evolve as frameworks and agentic systems change.

## Citation

If you use RADAR in your work, please cite:

> Lemos, Catarina. Measuring the Prevalence of Security Risks and Defenses in Open-Source Agentic Apps. Manuscript in preparation, 2026.

BibTeX:

```bibtex
@misc{lemos2026radar,
  title={Measuring the Prevalence of Security Risks and Defenses in Open-Source Agentic Apps},
  author={Lemos, Catarina},
  year={2026},
  note={Manuscript in preparation}
}
```

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Contact

For questions, collaboration requests, or technical issues related to this project, please contact the maintainer at:

- Name: Catarina Lemos
- Email: catarina.s.lemos@tecnico.ulisboa.pt
- GitHub: [@catalms](https://github.com/catalms)
- Affiliation: Instituto Superior Técnico, University of Lisbon

If you are reporting a bug or suggesting an improvement, please open an issue in the project repository.