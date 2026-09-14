"""
util.py

Small shared helpers used by both scanners:
  - consistent UTC timestamps (per-record AND baked into filenames)
  - a lightweight run-metadata "sidecar" file (started_at/finished_at
    for the whole run, not just each repo record) that's cheap to
    rewrite wholesale on every save, so it doesn't need the incremental
    streaming machinery the main results file uses.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def utc_now_compact():
    """Filesystem/filename-safe timestamp, e.g. 20260830T164512Z."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def timestamped_results_path(results_dir, prefix="pattern_scan_results"):
    """
    Default output path when the caller doesn't name one explicitly --
    guarantees every run's results file is timestamped and never
    silently overwrites a previous run's output.
    """
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir / f"{prefix}_{utc_now_compact()}.json"


def find_resumable_or_new_results_path(results_dir, prefix="pattern_scan_results"):
    """
    If the caller didn't name an output file, auto-timestamping a fresh
    filename on every invocation would silently break resume: interrupt
    a run, re-run the same command with no explicit path, and you'd get
    a brand-new empty file instead of continuing the old one. So: look
    for the most recent results file in results_dir whose meta sidecar
    says it never finished (finished_at is still null) and resume THAT
    one. Only mint a new timestamped file when there's nothing
    in-progress -- including right after a prior run completed
    successfully, which correctly starts a fresh dated snapshot rather
    than reopening a "done" report.
    """
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted(results_dir.glob(f"{prefix}_*.json"), reverse=True)  # timestamp format sorts newest-first
    for candidate in candidates:
        meta_path = meta_sidecar_path(candidate)
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if meta.get("finished_at") is None:
            return candidate
    return timestamped_results_path(results_dir, prefix=prefix)


def meta_sidecar_path(results_path):
    """<results file>.meta.json, sitting right next to the results file."""
    return Path(str(results_path) + ".meta.json")


class RunMetadata:
    """
    Tracks run-level (not per-repo) timing and counts, and persists to
    a small sidecar JSON file next to the main results file. Rewritten
    wholesale on every update -- it's small, so this is cheap and needs
    none of IncrementalJSONArrayWriter's append/resume machinery. On
    resume, `started_at` is preserved from the first run rather than
    reset, so the sidecar always reflects the true start of the whole
    (possibly multi-session) scan.
    """

    def __init__(self, results_path, source, patterns_path, repos_json_path):
        self.path = meta_sidecar_path(results_path)
        existing = self._load_existing()
        self.data = existing or {
            "results_file": str(results_path),
            "source": source,                     # "local" | "api"
            "patterns_path": str(patterns_path),
            "repos_json_path": str(repos_json_path),
            "started_at": utc_now_iso(),
            "finished_at": None,
            "sessions": 0,
        }
        self.data["sessions"] += 1
        self._save()

    def _load_existing(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _save(self):
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def finish(self, counts):
        self.data["finished_at"] = utc_now_iso()
        self.data["last_run_counts"] = counts
        self._save()
