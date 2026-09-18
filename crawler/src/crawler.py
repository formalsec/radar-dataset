#!/usr/bin/env python3
"""
GitHub Repo Crawler - Searches ALL languages at once
"""
import time
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
import requests
import base64
import gc

from config import (
    frameworks_manager,
    CRAWLER_OUTPUT_DIR,
    REQUIRED_LANGUAGES,
    MIN_LANGUAGE_PERCENTAGE,
    RESULTS_PER_PAGE,
    MAX_PAGES_PER_QUERY
)
from storage import StorageManager
from utils import (
    create_session, fetch_url, fetch_file,
    parse_package_json, parse_requirements_txt,
    calculate_language_percentage,
    get_all_languages_with_bytes
)


class RepoCrawler:
    def __init__(self, token: Optional[str] = None, target: int = 100):
        """Initialize the crawler"""
        self.token = token
        self.target = target
        
        # Setup session
        self.session = create_session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/vnd.github.v3+json'
        }
        if token:
            self.headers['Authorization'] = f'token {token}'
        
        # Storage
        self.storage = StorageManager()
        
        # Run tracking
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = CRAWLER_OUTPUT_DIR / f"crawl_results_{self.run_timestamp}"
        self.run_file = self.output_dir / f"run_{self.run_timestamp}.json"
        self.run_repos = []
        
        # Stats
        self.total_searched = 0
        self.total_duplicates = 0
        self.total_failed_language = 0
        self.total_failed_framework = 0
        self.total_passed = 0
        
        self.api_calls = 0
        
        # Create results directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Open result files
        self.passed_file = open(self.output_dir / "passed_urls.txt", 'w')
        self.failed_language_file = open(self.output_dir / "failed_language.txt", 'w')
        self.failed_framework_file = open(self.output_dir / "failed_framework.txt", 'w')
        self.all_searched_file = open(self.output_dir / "all_searched.txt", 'w')
        
        print(f"📁 Results will be saved in: {self.output_dir}/")
        print(f"📊 Target: {target} repos")
        print(f"🔍 Searching ALL languages at once (filtering to Python/JS/TS)")

    def run_group(self, group_name: str, queries: List[str]):
        """Run a group of queries - searches ALL languages at once"""
        print(f"\n{'='*60}")
        print(f"🚀 Starting Crawl: {group_name}")
        print(f"{'='*60}")
        print(f"Queries: {len(queries)}")
        print(f"Target: {self.target} repos")
        print(f"Language threshold: {MIN_LANGUAGE_PERCENTAGE}% Python/JS/TS")
        print(f"Searching: ALL languages (filtering after retrieval)")
        print(f"{'='*60}\n")
        
        # Initial stats
        self._print_stats()
        
        # Process each query - ONE call per query (ALL languages)
        for query_idx, query in enumerate(queries, 1):
            if self.total_passed >= self.target:
                print(f"\n🎯 Target reached! Found {self.total_passed}/{self.target} repos")
                break
            
            print(f"\n🔍 ({query_idx}/{len(queries)}): '{query}'")
            print(f"   (Searching ALL languages, filtering for Python/JS/TS)")
            print(f"   Need {self.target - self.total_passed} more repos")
            print("-" * 50)
            
            self._search_query(query)
        
        # Finalize
        self._finalize()

    def _search_query(self, query: str):
        """Search a single query - returns ALL languages"""
        for page in range(1, MAX_PAGES_PER_QUERY + 1):
            if self.total_passed >= self.target:
                break
            
            # Build URL - NO language prefix
            url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page={RESULTS_PER_PAGE}&page={page}"
            
            # Fetch
            print(f"   📄 Page {page}/{MAX_PAGES_PER_QUERY}...", end=" ")
            response = self._fetch_url(url)
            self.api_calls += 1
            
            # Initialize repos variable
            repos = []
            
            if not response or 'items' not in response:
                print("❌ No response")
                break
            
            repos = response['items']
            if not repos:
                print("📭 No repos")
                break
            
            print(f"got {len(repos)} repos")
            total_count = response.get('total_count', 0)
            print(f"   📊 Total results for this query: {total_count:,}")
            
            # Process repos
            page_new = 0
            page_dupes = 0
            page_failed_lang = 0
            page_failed_fw = 0
            
            for repo in repos:
                if self.total_passed >= self.target:
                    break
                
                # Track repo
                url = repo['html_url']
                name = repo['full_name']
                
                # Check if seen before
                if self.storage.is_seen(url, name):
                    page_dupes += 1
                    self.total_duplicates += 1
                    continue
                
                # Mark as seen
                self.storage.mark_seen(url, name)
                self.total_searched += 1
                
                # Write to all searched
                self.all_searched_file.write(f"{url}\n")
                self.all_searched_file.flush()
                
                # Analyze repo
                result = self._analyze_repo(repo, query)
                
                if result == 'passed':
                    page_new += 1
                    self.total_passed += 1
                elif result == 'failed_language':
                    page_failed_lang += 1
                    self.total_failed_language += 1
                elif result == 'failed_framework':
                    page_failed_fw += 1
                    self.total_failed_framework += 1
            
            # Print page summary
            print(f"   📊 Page {page}: new={page_new}, dupes={page_dupes}, failed_lang={page_failed_lang}, failed_fw={page_failed_fw}")
            
            # Print current stats
            self._print_stats()
            
            # Save progress
            self.storage.flush()
            
            # Check if last page
            if len(repos) < RESULTS_PER_PAGE:
                print(f"   📭 Last page (got {len(repos)} < {RESULTS_PER_PAGE})")
                break
            
            # Free memory
            del repos
            gc.collect()
            
            time.sleep(0.5)

    def _analyze_repo(self, repo: Dict, query: str):
        """Analyze a single repository"""
        name = repo['full_name']
        url = repo['html_url']
        stars = repo['stargazers_count']
        
        print(f"\n   📊 Analyzing: {name} (⭐{stars:,})")
        
        # 1. Check language percentage
        lang_result = self._check_languages(name)
        
        if not lang_result['passed']:
            print(f"      ❌ Language: {lang_result['percentage']:.1f}% Python/JS/TS (need {MIN_LANGUAGE_PERCENTAGE}%)")
            self.failed_language_file.write(f"{url} - {lang_result['percentage']:.1f}%\n")
            self.failed_language_file.flush()
            self.storage.add_failed_language(url, name, lang_result['percentage'])
            return 'failed_language'
        
        print(f"      ✅ Language: {lang_result['percentage']:.1f}% Python/JS/TS")
        
        # 2. Check for frameworks
        frameworks = self._detect_frameworks(name)
        
        if not frameworks:
            print(f"      ❌ No frameworks detected")
            self.failed_framework_file.write(f"{url} - No frameworks\n")
            self.failed_framework_file.flush()
            self.storage.add_failed_framework(url, name)
            return 'failed_framework'
        
        # 3. Passed all checks!
        print(f"      ✅ Found frameworks: {', '.join([f['name'] for f in frameworks])}")
        
        # Create repo data
        repo_data = {
            'name': name,
            'url': url,
            'stars': stars,
            'language_percentage': round(lang_result['percentage'], 2),
            'languages': lang_result['languages'][:5],
            'frameworks': frameworks,
            'found_via': query,
            'found_at': datetime.now().isoformat()
        }
        
        # Save to storage
        if self.storage.add_passed_repo(repo_data):
            self.run_repos.append(repo_data)
            
            # Write to passed file
            self.passed_file.write(f"{url}\n")
            self.passed_file.flush()
            
            # Save to run file
            self._save_run_file()
            
            return 'passed'
        
        return 'failed_framework'

    def _check_languages(self, repo: str) -> Dict:
        """Check language composition"""
        url = f"https://api.github.com/repos/{repo}/languages"
        data = self._fetch_url(url)
        self.api_calls += 1
        
        if not data:
            return {'passed': False, 'percentage': 0, 'languages': {}}
        
        total = sum(data.values())
        if total == 0:
            return {'passed': False, 'percentage': 0, 'languages': {}}
        
        # Calculate Python/JS/TS percentage
        py_js_ts = sum(data.get(lang, 0) for lang in REQUIRED_LANGUAGES)
        percentage = (py_js_ts / total) * 100
        
        # Get language list with percentages
        languages = []
        for lang, bytes_count in sorted(data.items(), key=lambda x: x[1], reverse=True)[:5]:
            languages.append({
                'name': lang,
                'percentage': (bytes_count / total) * 100
            })
        
        return {
            'passed': percentage >= MIN_LANGUAGE_PERCENTAGE,
            'percentage': percentage,
            'languages': languages
        }

    def _detect_frameworks(self, repo: str) -> List[Dict]:
        """Detect frameworks from dependencies or README"""
        detected = []
        
        # Try package.json
        package_json = self._fetch_file(repo, 'package.json')
        if package_json:
            self.api_calls += 1
            deps = parse_package_json(package_json)
            if deps:
                detected = frameworks_manager.detect_frameworks_from_dependencies(deps, 'package.json')
                if detected:
                    return detected
        
        # Try requirements.txt
        requirements = self._fetch_file(repo, 'requirements.txt')
        if requirements:
            self.api_calls += 1
            deps = parse_requirements_txt(requirements)
            if deps:
                detected = frameworks_manager.detect_frameworks_from_dependencies(deps, 'requirements.txt')
                if detected:
                    return detected
        
        # Try README.md as fallback
        readme = self._fetch_file(repo, 'README.md')
        if readme:
            self.api_calls += 1
            detected = frameworks_manager.detect_frameworks_from_text(readme)
            if detected:
                return detected
        
        return []

    def _fetch_url(self, url: str) -> Optional[Dict]:
        """Fetch JSON from URL with retries"""
        for attempt in range(3):
            try:
                response = self.session.get(url, headers=self.headers, timeout=15)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 403 and 'rate limit' in response.text.lower():
                    wait = 60 * (attempt + 1)
                    print(f"⏳ Rate limit, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                elif response.status_code == 404:
                    return None
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
        return None

    def _fetch_file(self, repo: str, file_path: str) -> Optional[str]:
        """Fetch file content"""
        url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
        try:
            response = self.session.get(url, headers=self.headers, timeout=10)
            self.api_calls += 1
            if response.status_code == 200:
                data = response.json()
                if 'content' in data:
                    content = base64.b64decode(data['content']).decode('utf-8', errors='ignore')
                    return content
            return None
        except:
            return None

    def _print_stats(self):
        """Print current statistics"""
        print(f"\n📊 CURRENT STATS:")
        print(f"   Total searched: {self.total_searched:,}")
        print(f"   Duplicates skipped: {self.total_duplicates:,}")
        print(f"   Failed language: {self.total_failed_language:,}")
        print(f"   Failed framework: {self.total_failed_framework:,}")
        print(f"   ✅ PASSED: {self.total_passed:,} (target: {self.target})")
        print(f"   API calls: {self.api_calls:,}")

    def _save_run_file(self):
        """Save current run data"""
        run_data = {
            'metadata': {
                'timestamp': self.run_timestamp,
                'target': self.target,
                'total_searched': self.total_searched,
                'total_passed': self.total_passed,
                'total_failed_language': self.total_failed_language,
                'total_failed_framework': self.total_failed_framework,
                'total_duplicates': self.total_duplicates,
                'api_calls': self.api_calls
            },
            'repos': self.run_repos
        }
        with open(self.run_file, 'w') as f:
            json.dump(run_data, f, indent=2)

    def _finalize(self):
        """Finalize the run"""
        # Flush storage
        self.storage.flush()
        
        # Close files
        self.passed_file.close()
        self.failed_language_file.close()
        self.failed_framework_file.close()
        self.all_searched_file.close()
        
        # Save run file
        self._save_run_file()
        
        # Print final stats
        print(f"\n{'='*60}")
        print(f"✅ RUN COMPLETE!")
        print(f"{'='*60}")
        print(f"\n📊 FINAL STATISTICS:")
        print(f"   Total searched: {self.total_searched:,}")
        print(f"   Duplicates skipped: {self.total_duplicates:,}")
        print(f"   Failed language: {self.total_failed_language:,}")
        print(f"   Failed framework: {self.total_failed_framework:,}")
        print(f"   ✅ PASSED: {self.total_passed:,}")
        print(f"   API calls: {self.api_calls:,}")
        
        print(f"\n📁 Results saved:")
        print(f"   Master: {self.storage.master_file}")
        print(f"   Cache: {self.storage.cache_file}")
        print(f"   Run file: {self.run_file}")
        print(f"   Output dir: {self.output_dir}/")
        print(f"{'='*60}")

    def print_summary(self):
        """Print summary of all data"""
        stats = self.storage.get_stats()
        print(f"\n📊 SUMMARY (All Time):")
        print(f"   Total searched: {stats['total_searched']:,}")
        print(f"   Total passed: {stats['total_passed']:,}")
        print(f"   Failed language: {stats['total_failed_language']:,}")
        print(f"   Failed framework: {stats['total_failed_framework']:,}")
        