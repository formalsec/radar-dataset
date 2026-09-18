"""
aggregate.py

Reads a scan_api.py output file (a JSON array of per-repo records, possibly
still being written to) and reports n_agents/n_tools per repo plus totals
across the corpus. Uses incremental_json.repair_and_load so it also works
on a scan that's still in progress -- any trailing partial record is
dropped rather than crashing the load.

Run with:
    python -m pattern_scan.aggregate pattern_scan/results/full_corpus_scan.json
    python -m pattern_scan.aggregate pattern_scan/results/full_corpus_scan.json --csv out.csv
"""

import argparse
import csv
import json
import sys

from .incremental_json import repair_and_load


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scan_json", help="output file from scan_api.py")
    ap.add_argument("--csv", default=None, help="also write per-repo counts to this CSV path")
    ap.add_argument("--totals-json", default=None, help="also write corpus-wide totals to this JSON path")
    ap.add_argument("--top", type=int, default=20, help="how many repos to print, ranked by n_agents (default 20)")
    args = ap.parse_args()

    records = repair_and_load(args.scan_json)
    scanned = [r for r in records if r.get("status") == "scanned" and r.get("radar_summary")]
    failed = [r for r in records if r.get("status") != "scanned"]

    rows = []
    for r in scanned:
        rs = r["radar_summary"]
        rows.append({
            "repo_name": r.get("repo_name"),
            "stars": r.get("stars"),
            "n_agents": rs.get("n_agents", 0),
            "n_custom_agents": rs.get("n_custom_agents", 0),
            "n_tools": rs.get("n_tools", 0),
            "has_rag": rs.get("has_rag", False),
        })

    rows.sort(key=lambda x: x["n_agents"], reverse=True)

    total_agents = sum(x["n_agents"] for x in rows)
    total_custom_agents = sum(x["n_custom_agents"] for x in rows)
    total_tools = sum(x["n_tools"] for x in rows)
    n_repos_with_agents = sum(1 for x in rows if x["n_agents"] > 0)
    n_repos_with_tools = sum(1 for x in rows if x["n_tools"] > 0)
    n_repos_with_rag = sum(1 for x in rows if x["has_rag"])

    print(f"Records in file: {len(records)}  (scanned={len(scanned)}, failed={len(failed)})\n")

    print(f"{'repo_name':<50} {'stars':>8} {'n_agents':>9} {'n_custom':>9} {'n_tools':>8} {'has_rag':>8}")
    for x in rows[:args.top]:
        print(f"{x['repo_name']:<50} {x['stars'] or 0:>8} {x['n_agents']:>9} "
              f"{x['n_custom_agents']:>9} {x['n_tools']:>8} {str(x['has_rag']):>8}")
    if len(rows) > args.top:
        print(f"... ({len(rows) - args.top} more repos, use --top to see more or --csv for the full table)")

    print("\n--- Corpus totals ---")
    print(f"repos scanned:                {len(scanned)}")
    print(f"repos failed:                 {len(failed)}")
    print(f"total n_agents:               {total_agents}")
    print(f"total n_custom_agents:        {total_custom_agents}")
    print(f"total n_tools:                {total_tools}")
    print(f"repos with >=1 agent:         {n_repos_with_agents}")
    print(f"repos with >=1 tool:          {n_repos_with_tools}")
    print(f"repos with RAG/memory store:  {n_repos_with_rag}")

    if failed:
        print(f"\n--- Failed repos ({len(failed)}) ---")
        for r in failed:
            print(f"  {r.get('repo_name')}: {r.get('error')}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["repo_name", "stars", "n_agents",
                                                     "n_custom_agents", "n_tools", "has_rag"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote per-repo CSV to {args.csv}")

    if args.totals_json:
        totals = {
            "repos_in_file": len(records),
            "repos_scanned": len(scanned),
            "repos_failed": len(failed),
            "total_n_agents": total_agents,
            "total_n_custom_agents": total_custom_agents,
            "total_n_tools": total_tools,
            "repos_with_at_least_1_agent": n_repos_with_agents,
            "repos_with_at_least_1_tool": n_repos_with_tools,
            "repos_with_rag": n_repos_with_rag,
        }
        with open(args.totals_json, "w", encoding="utf-8") as f:
            json.dump(totals, f, indent=2)
        print(f"Wrote corpus totals to {args.totals_json}")


if __name__ == "__main__":
    sys.exit(main())
