#!/usr/bin/env python3
"""
GitHub Repo Crawler - Main Entry Point
"""
import os
import sys
import argparse
from crawler import RepoCrawler
from config import SEARCH_QUERY_GROUPS, DEFAULT_TARGET_REPOS


def main():
    parser = argparse.ArgumentParser(
        description='GitHub Repo Crawler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available groups:
  {', '.join(SEARCH_QUERY_GROUPS.keys())}

Examples:
  # Run with token and target
  python main.py --group agent_frameworks --target 100 --token YOUR_TOKEN
  
  # Reset cache and start fresh
  python main.py --group comprehensive --target 200 --reset-cache --token YOUR_TOKEN
  
  # List available groups
  python main.py --list-groups
        """
    )
    
    parser.add_argument('--group', required=True, help='Query group to run')
    parser.add_argument('--token', help='GitHub API token (or set GITHUB_TOKEN env var)')
    parser.add_argument('--target', type=int, default=DEFAULT_TARGET_REPOS,
                       help=f'Number of repos to find (default: {DEFAULT_TARGET_REPOS})')
    parser.add_argument('--reset-cache', action='store_true',
                       help='Reset the duplicate cache and start fresh')
    parser.add_argument('--list-groups', action='store_true',
                       help='List available query groups')
    
    args = parser.parse_args()
    
    # List groups
    if args.list_groups:
        print("\n📁 Available query groups:")
        for group_name, queries in SEARCH_QUERY_GROUPS.items():
            print(f"  • {group_name}: {len(queries)} queries")
        return
    
    # Validate group
    if args.group not in SEARCH_QUERY_GROUPS:
        print(f"❌ Unknown group: {args.group}")
        print(f"Available: {', '.join(SEARCH_QUERY_GROUPS.keys())}")
        return
    
    # Get token
    token = args.token or os.environ.get('GITHUB_TOKEN')
    
    if not token:
        print("⚠️ No token provided - rate limit will be 60/hour")
        print("   Get token at https://github.com/settings/tokens\n")
    
    # Get queries
    queries = SEARCH_QUERY_GROUPS[args.group]
    
    print(f"\n📁 Group: {args.group}")
    print(f"📊 Queries: {len(queries)}")
    print(f"🎯 Target: {args.target} repos")
    
    # Reset cache if requested
    if args.reset_cache:
        from storage import StorageManager
        storage = StorageManager()
        storage.clear_cache()
    
    # Run crawler
    crawler = RepoCrawler(token=token, target=args.target)
    crawler.run_group(args.group, queries)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user!")
        print("💾 Progress saved incrementally.")
        print("✅ Resume by running the same command again.")
        sys.exit(0)