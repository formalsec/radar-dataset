"""
github_fetcher.py

Fetches RADAR corpus repos via the GitHub REST API instead of `git
clone`: pulls Table IV metadata (stars, forks, primary language, last
commit, ...) AND the repo's source tree (via the tarball endpoint) in
one pass per repo, with no `git` binary dependency.

Requires a GitHub personal access token -- set GITHUB_TOKEN in the
environment, or pass --token. 516 repos x 2 API calls (metadata +
tarball) = ~1,032 calls; comfortably inside the 5,000/req/hr
authenticated budget in one run. Unauthenticated (60/hr) is not
practical at this corpus size.

Produces the SAME manifest shape as repo_fetcher.py
(id/url/local_path/status/error), plus a `metadata` field per entry,
so ast_index.py's build_corpus_index() works against it unchanged --
this is meant as a drop-in replacement, not a parallel format.

Usage:
    python github_fetcher.py <repo_list_file> <dest_dir> [--token TOKEN] [--max-workers N]
"""

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------- #
# Repo-list loading (same formats as repo_fetcher.py)
# ---------------------------------------------------------------- #

def _parse_owner_repo(entry):
    """Accept 'owner/repo', a full github.com URL, or an SSH URL; return (owner, repo) or None."""
    entry = entry.strip()
    if not entry:
        return None
    entry = re.sub(r"^https?://github\.com/", "", entry)
    entry = re.sub(r"^git@github\.com:", "", entry)
    entry = entry.rstrip("/")
    if entry.endswith(".git"):
        entry = entry[: -len(".git")]
    parts = entry.split("/")
    if len(parts) < 2 or not parts[-1] or not parts[-2]:
        return None
    return parts[-2], parts[-1]


def load_repo_list(list_path):
    path = Path(list_path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = []
        for item in data:
            if isinstance(item, str):
                entries.append(item)
            elif isinstance(item, dict):
                entries.append(item.get("url") or item.get("repo") or item.get("name"))
        return [e for e in entries if e]

    if suffix == ".csv":
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            key = None
            for candidate in ("url", "repo", "repository", "name"):
                if reader.fieldnames and candidate in reader.fieldnames:
                    key = candidate
                    break
            if key is None:
                f.seek(0)
                return [row[0] for row in csv.reader(f) if row and row[0].strip()]
            return [row[key] for row in reader if row.get(key)]

    lines = path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.strip().startswith("#")]


# ---------------------------------------------------------------- #
# Pure tarball-extraction logic (kept separate so it's testable
# without hitting the network)
# ---------------------------------------------------------------- #

def extract_tarball(tar_bytes, dest_path):
    """
    Extracts a GitHub tarball into dest_path, stripping the single
    top-level '<owner>-<repo>-<sha>/' directory GitHub always wraps
    the archive in, so dest_path ends up being the repo root itself.
    Returns an error string, or None on success.
    """
    dest_path = Path(dest_path)
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            members = tar.getmembers()
            if not members:
                return "empty tarball"
            top = members[0].name.split("/")[0]
            dest_path.mkdir(parents=True, exist_ok=True)
            for member in members:
                if not member.name.startswith(top + "/"):
                    continue
                member.name = member.name[len(top) + 1:]
                if not member.name:
                    continue
                tar.extract(member, path=dest_path)
    except tarfile.TarError as e:
        return f"tarball extract failed: {e}"
    return None


# ---------------------------------------------------------------- #
# GitHub API client
# ---------------------------------------------------------------- #

class GitHubClient:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _get(self, url, timeout=30, max_retries=3):
        resp = None
        for attempt in range(max_retries):
            resp = self.session.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429):
                retry_after = resp.headers.get("Retry-After")
                remaining = resp.headers.get("X-RateLimit-Remaining")
                reset = resp.headers.get("X-RateLimit-Reset")
                if retry_after:
                    time.sleep(int(retry_after) + 1)
                elif remaining == "0" and reset:
                    wait = max(0, int(reset) - int(time.time())) + 2
                    print(f"  rate limited, sleeping {wait}s...")
                    time.sleep(wait)
                else:
                    # secondary/abuse-detection limit -- back off and retry
                    time.sleep(2 ** attempt * 5)
                continue
            return resp  # 404 etc. -- let the caller report it
        return resp

    def get_metadata(self, owner, repo):
        resp = self._get(f"{GITHUB_API}/repos/{owner}/{repo}")
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "no response"
            body = resp.text[:200] if resp is not None else ""
            return None, f"metadata fetch failed ({code}): {body}"
        data = resp.json()
        metadata = {
            "full_name": data.get("full_name"),
            "html_url": data.get("html_url"),
            "created_at": data.get("created_at"),
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "last_commit": data.get("pushed_at"),
            "primary_language": data.get("language"),
            "default_branch": data.get("default_branch"),
            "size_kb": data.get("size"),
            "archived": data.get("archived"),
        }
        return metadata, None

    def download_tarball(self, owner, repo, ref, dest_path):
        resp = self._get(f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{ref}")
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "no response"
            return f"tarball fetch failed ({code})"
        return extract_tarball(resp.content, dest_path)


# ---------------------------------------------------------------- #
# Per-repo + corpus driver
# ---------------------------------------------------------------- #

def fetch_one(client, entry_str, dest_dir):
    parsed = _parse_owner_repo(entry_str)
    if parsed is None:
        return {"id": entry_str, "url": entry_str, "local_path": None,
                "status": "failed", "error": "could not parse owner/repo", "metadata": None}

    owner, repo = parsed
    repo_id = f"{owner}__{repo}"
    url = f"https://github.com/{owner}/{repo}"
    local_path = Path(dest_dir) / repo_id

    if local_path.exists():
        return {"id": repo_id, "url": url, "local_path": str(local_path),
                "status": "skipped_exists", "error": None, "metadata": None}

    metadata, meta_err = client.get_metadata(owner, repo)
    if meta_err:
        return {"id": repo_id, "url": url, "local_path": None,
                "status": "failed", "error": meta_err, "metadata": None}

    ref = metadata["default_branch"] or "HEAD"
    tar_err = client.download_tarball(owner, repo, ref, local_path)
    if tar_err:
        shutil.rmtree(local_path, ignore_errors=True)
        return {"id": repo_id, "url": url, "local_path": None,
                "status": "failed", "error": tar_err, "metadata": metadata}

    return {"id": repo_id, "url": url, "local_path": str(local_path),
            "status": "cloned", "error": None, "metadata": metadata}


def fetch_all(list_path, dest_dir, token=None, max_workers=4):
    """
    max_workers defaults lower than repo_fetcher.py's git-based version:
    GitHub applies a secondary rate limit to concurrent requests
    independent of the 5,000/hr budget, and going wide trips it.
    """
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("No GitHub token found. Set GITHUB_TOKEN or pass --token.")

    client = GitHubClient(token)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    raw_entries = load_repo_list(list_path)
    seen, entries = set(), []
    for e in raw_entries:
        key = _parse_owner_repo(e) or e
        if key not in seen:
            seen.add(key)
            entries.append(e)

    manifest = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, client, e, dest_dir): e for e in entries}
        for future in as_completed(futures):
            result = future.result()
            manifest.append(result)
            done += 1
            print(f"[{done}/{len(entries)}] {result['status']:15s} {result['id']}")

    manifest.sort(key=lambda e: e["id"])
    manifest_path = dest_dir / "repos_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    n_ok = sum(1 for e in manifest if e["status"] in ("cloned", "skipped_exists"))
    n_failed = sum(1 for e in manifest if e["status"] == "failed")
    print(f"\nFetched/present: {n_ok}  Failed: {n_failed}")
    if n_failed:
        print("Failed repos are recorded in the manifest with their error --")
        print("re-run this script to retry just those (successful ones are skipped).")
    print(f"Manifest written to {manifest_path}")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch RADAR repos via the GitHub API.")
    parser.add_argument("repo_list", help="txt/csv/json file of owner/repo entries or URLs")
    parser.add_argument("dest_dir", help="local directory to fetch into")
    parser.add_argument("--token", default=None, help="GitHub PAT (else reads GITHUB_TOKEN env var)")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    fetch_all(args.repo_list, args.dest_dir, token=args.token, max_workers=args.max_workers)