# Repo Classifier — Summary

Both scripts are the **same repo classifier**, parameterized by which model you point them at (`--model/-m`): one talks to local Ollama models (default `llama3.3`), the other to hosted GPT models (default `gpt-5.4-mini`). Together they form (at least) two of your three judges.

## What it does
Given a list of GitHub URLs, it fetches each repo's README and asks the model whether the repo is primarily an `app`, `framework`, or `needs_review`, returning a JSON verdict with a brief reasoning.

## How it works
- **README fetching:** GitHub REST API → raw.githubusercontent.com (multiple branches/filenames) → HTML scrape, with retries.
- **Prompt:** defines "app" vs. "framework," asks for a single label plus reasoning, and requests strict JSON output.
- **Parsing:** tries to extract JSON from the response first; if that fails, falls back to keyword matching to guess the label.
- **Output:** a single JSON file per run, with one entry per URL (classification, reasoning, error, timestamp) plus running totals for `app`/`framework`/`needs_review`/`error`.
- **Resilience:** saves incrementally every 10 results, keeps a rotating backup, handles Ctrl+C gracefully, and supports `--resume` to pick up where it left off.

## Worth flagging
- **`needs_review` is overloaded:** it's used both for genuine model uncertainty and as a catch-all when parsing or fetching fails, which could muddy any agreement statistics computed from these outputs.
- These are good concrete examples to cite alongside your κ ≈ 0.10 finding if you want to illustrate mechanical (not just conceptual) sources of judge noise.