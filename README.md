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
- crawler/classifications/: LLM-based classifiers for separating applications from frameworks
- toolkit/: static-analysis tooling for scanning repository code
- toolkit/patterns/: human-authored pattern definitions used to detect agentic constructs and risky data flows
- toolkit/pattern_scan/: scanner and result aggregation code
- results and JSON artifacts: examples of intermediate and final outputs

## Dataset and methodology

The final corpus is a set of agentic applications collected from GitHub and selected to reflect diversity across frameworks, domains, and autonomy levels.

The acquisition pipeline is:

1. GitHub search for repositories mentioning agent terms and primarily implemented in Python, JavaScript, or TypeScript.
2. Language composition filter: keep repositories where at least 70% of code is in those languages.
3. Framework dependency filter: keep repositories that import or declare a dependency on at least one agent-development framework or protocol in the allow-list.
4. Application vs. framework classification using multiple LLM judges plus manual review for ambiguous cases.
5. Repository download and static analysis of the surviving applications.

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

The crawler configuration lives in crawler/config.py. It defines the language scopes, search queries, and repository-selection thresholds.

```bash
python crawler/crawler.py
```

The crawler writes intermediate results into timestamped directories under crawler/results_*/ and retains the repositories that pass both the language and framework filters.

### 2) Classify repositories as applications vs. frameworks

The classification step is handled under crawler/classifications/.

For Ollama-based classification:

```bash
ollama serve
python crawler/classifications/repoClassifier.py \
  --input urls_to_classify.txt \
  --output classifications.json \
  --model llama3.3
```

For GPT-based classification:

```bash
python crawler/classifications/repoClassifierGPT.py \
  --input urls_to_classify.txt \
  --output classifications_gpt.json \
  --model gpt-5.4-mini
```

The scripts accept `--limit` and `--resume` options and write aggregate counts together with model reasoning and errors.

### 3) Download the corpus repositories

Use the fetcher to download repository source code for the retained application set.

```bash
python toolkit/github_fetcher.py <repo_list_file> <dest_dir> [--token TOKEN] [--max-workers N]
```

Example:

```bash
python toolkit/github_fetcher.py passed_urls.txt ./repos --max-workers 8
```

### 4) Run the static scanner

The scanner takes a JSON manifest of repositories (for example, the downloaded corpus or a filtered subset) and then analyzes the referenced repository source code to emit structured findings per repository.

```bash
python -m toolkit.pattern_scan.scan_api <repos_json> [output_file] [--patterns FILE] [--token TOKEN] [--max-file-size BYTES] [--limit N] [--gc-every N]
```

Example:

```bash
python -m toolkit.pattern_scan.scan_api corpus.json results.json --limit 50
```

## Output format

The scanner produces records with repository summaries and evidence. Typical fields include:

- repo_name, repo_url, declared_frameworks
- status, error, scanned_at
- radar_summary with values such as n_agents, n_tools, has_rag, shared_across_agents, rag_writers, rag_readers, unsanitized_writes
- findings_by_file and other traceable evidence for each repository

The fetcher also writes a manifest with repository metadata such as URL, local path, status, and GitHub metadata when available.

## Pattern definitions

The rules for matching relevant code patterns are kept in markdown files under toolkit/patterns/:

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