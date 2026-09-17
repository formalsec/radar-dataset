#!/usr/bin/env python3
"""
Storage Manager - Incremental saving with duplicate prevention
"""
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Set


class StorageManager:
    def __init__(self):
        self.master_file = 'all_repos.json'
        self.searched_urls_file = 'searched_urls.json'
        self.cache_file = 'seen_repos.json'
        
        # Load existing data
        self.passed_repos = self._load_passed_repos()
        self.searched_urls = self._load_searched_urls()
        self.seen_urls = self._load_seen_urls()
        self.seen_names = set()
        
        # Build seen names from passed repos
        for repo in self.passed_repos:
            if 'name' in repo:
                self.seen_names.add(repo['name'])
        
        # Stats
        self.stats = {
            'total_searched': len(self.searched_urls),
            'total_passed': len(self.passed_repos),
            'total_failed_language': 0,
            'total_failed_framework': 0,
        }
        
        # Pending writes
        self.pending_repos = []
        self.pending_searched = []
        
        # Batch settings
        self.save_batch_size = 10

    def _load_passed_repos(self) -> List[Dict]:
        """Load repos that passed all checks"""
        if os.path.exists(self.master_file):
            try:
                with open(self.master_file, 'r') as f:
                    data = json.load(f)
                    return data.get('repos', [])
            except:
                return []
        return []

    def _load_searched_urls(self) -> Set[str]:
        """Load all URLs that have been searched"""
        if os.path.exists(self.searched_urls_file):
            try:
                with open(self.searched_urls_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('urls', []))
            except:
                return set()
        return set()

    def _load_seen_urls(self) -> Set[str]:
        """Load cache of seen URLs (all repos ever encountered)"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('urls', []))
            except:
                return set()
        return set()

    def is_seen(self, url: str, name: str) -> bool:
        """Check if repo has been seen before"""
        return url in self.seen_urls or name in self.seen_names

    def mark_seen(self, url: str, name: str):
        """Mark repo as seen"""
        self.seen_urls.add(url)
        self.seen_names.add(name)
        self.pending_searched.append(url)

    def is_duplicate(self, url: str) -> bool:
        """Check if URL is already in passed repos"""
        return url in self.searched_urls

    def add_passed_repo(self, repo_data: Dict) -> bool:
        """Add a repo that passed all checks"""
        url = repo_data.get('url')
        if url and self.is_duplicate(url):
            return False
        
        self.passed_repos.append(repo_data)
        self.pending_repos.append(repo_data)
        self.searched_urls.add(url)
        
        # Auto-save if batch size reached
        if len(self.pending_repos) >= self.save_batch_size:
            self.flush()
        
        return True

    def add_failed_language(self, url: str, name: str, percentage: float):
        """Track a repo that failed language check"""
        self.stats['total_failed_language'] += 1
        # Could log to file if needed

    def add_failed_framework(self, url: str, name: str):
        """Track a repo that failed framework check"""
        self.stats['total_failed_framework'] += 1
        # Could log to file if needed

    def flush(self):
        """Save all pending data"""
        if self.pending_repos:
            self._save_master()
        if self.pending_searched:
            self._save_cache()
        if self.pending_searched:
            self._save_searched_urls()
        
        self.pending_repos = []
        self.pending_searched = []

    def _save_master(self):
        """Save master file"""
        data = {
            'metadata': {
                'total_repos': len(self.passed_repos),
                'last_updated': datetime.now().isoformat(),
                'min_language_percentage': 70.0
            },
            'repos': self.passed_repos
        }
        with open(self.master_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _save_cache(self):
        """Save cache file"""
        data = {
            'urls': list(self.seen_urls),
            'names': list(self.seen_names),
            'total_seen': len(self.seen_urls),
            'last_updated': datetime.now().isoformat()
        }
        with open(self.cache_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _save_searched_urls(self):
        """Save searched URLs file"""
        data = {
            'urls': list(self.searched_urls),
            'total_searched': len(self.searched_urls),
            'last_updated': datetime.now().isoformat()
        }
        with open(self.searched_urls_file, 'w') as f:
            json.dump(data, f, indent=2)

    def get_stats(self) -> Dict:
        """Get current statistics"""
        return {
            'total_searched': len(self.searched_urls),
            'total_passed': len(self.passed_repos),
            'total_failed_language': self.stats['total_failed_language'],
            'total_failed_framework': self.stats['total_failed_framework'],
            'pending_saves': len(self.pending_repos)
        }

    def get_passed_repos(self) -> List[Dict]:
        """Get all passed repos"""
        return self.passed_repos

    def get_searched_urls(self) -> Set[str]:
        """Get all searched URLs"""
        return self.searched_urls

    def clear_cache(self):
        """Clear ALL cached data for a complete reset"""
        # Clear memory
        self.seen_urls = set()
        self.seen_names = set()
        self.searched_urls = set()
        self.passed_repos = []
        self.pending_repos = []
        self.pending_searched = []
        
        # Reset stats
        self.stats = {
            'total_searched': 0,
            'total_passed': 0,
            'total_failed_language': 0,
            'total_failed_framework': 0,
        }
        
        # Delete all files
        files_to_delete = [
            self.cache_file,      # seen_repos.json
            self.searched_urls_file,  # searched_urls.json
            self.master_file,     # all_repos.json
        ]
        
        deleted_count = 0
        for file in files_to_delete:
            if os.path.exists(file):
                os.remove(file)
                print(f"  ✅ Deleted: {file}")
                deleted_count += 1
        
        if deleted_count == 0:
            print("  ℹ️ No cache files found to delete")
        else:
            print(f"🗑️ Cache completely cleared ({deleted_count} files deleted)")
            
    def get_cached_count(self) -> int:
        """Get number of cached repos"""
        return len(self.seen_urls)

    