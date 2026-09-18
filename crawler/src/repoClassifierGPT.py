import os
import json
import glob
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import re
import shutil
from urllib.parse import urlparse
import base64
import signal
import sys
import gc

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLASSIFICATIONS_DIR = os.path.join(PROJECT_ROOT, 'crawler', 'output', 'llm_classifications')

# Import your GPTClient
from gpt import GPTClient

class URLClassifier:
    """Classify URLs using GPT - with improved error handling and memory optimization"""
    
    def __init__(self, model: str = "gpt-5.4-mini"):
        """
        Initialize the classifier
        
        Args:
            model: Model to use for generation
        """
        self.client = GPTClient(model=model)
        self.model_name = model
        self.results_file = None
        self.current_results = None
        self.counts = {
            'app': 0,
            'framework': 0,
            'needs_review': 0,
            'error': 0
        }
        
    def signal_handler(self, sig, frame):
        """Handle Ctrl+C to save progress"""
        print("\n\n⚠️ Interrupted! Saving progress...")
        if self.current_results and self.results_file:
            self.save_results(self.current_results, self.results_file)
        sys.exit(0)
    
    def update_counts(self, results):
        """Update classification counters from results"""
        self.counts = {
            'app': 0,
            'framework': 0,
            'needs_review': 0,
            'error': 0
        }
        
        for result in results.get('results', []):
            if result.get('error'):
                self.counts['error'] += 1
            elif result.get('classification'):
                classification = result['classification']
                if classification in self.counts:
                    self.counts[classification] += 1
        
        results['counts'] = self.counts.copy()
    
    def truncate_content(self, content, max_chars=4000):
        """Helper to truncate content to avoid token limits"""
        if not content:
            return content
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [truncated]"
        return content
    
    def extract_github_repo(self, url):
        """Extract owner/repo from GitHub URL"""
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        parts = path.split('/')
        if len(parts) >= 2 and 'github.com' in parsed.netloc:
            return f"{parts[0]}/{parts[1]}"
        return None
    
    def check_rate_limit(self):
        """Check GitHub API rate limit status"""
        try:
            url = "https://api.github.com/rate_limit"
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'RepoClassifier/1.0'
            }
            
            github_token = os.environ.get('GITHUB_TOKEN')
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                remaining = data['resources']['core']['remaining']
                limit = data['resources']['core']['limit']
                reset = data['resources']['core']['reset']
                reset_time = datetime.fromtimestamp(reset)
                
                print(f"📊 GitHub API Rate Limit: {remaining}/{limit} requests remaining")
                print(f"   Resets at: {reset_time.strftime('%Y-%m-%d %H:%M:%S')}")
                
                if remaining < 10:
                    print(f"⚠️  LOW RATE LIMIT! Only {remaining} requests remaining.")
                
                return remaining
            else:
                print(f"⚠️  Could not check rate limit: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"⚠️  Error checking rate limit: {e}")
            return None
    
    def get_readme_content(self, repo_url: str) -> Optional[str]:
        """
        Enhanced README fetching with more strategies and rate limit handling
        """
        repo_name = self.extract_github_repo(repo_url)
        if not repo_name:
            return None
        
        # Setup headers with authentication
        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'RepoClassifier/1.0'
        }
        
        github_token = os.environ.get('GITHUB_TOKEN')
        if github_token:
            headers['Authorization'] = f'token {github_token}'
        
        # 1. Try GitHub API first (most reliable)
        try:
            api_url = f"https://api.github.com/repos/{repo_name}/readme"
            response = requests.get(api_url, headers=headers, timeout=10)
            
            # Check for rate limiting
            if response.status_code == 403:
                remaining = response.headers.get('X-RateLimit-Remaining')
                reset_time = response.headers.get('X-RateLimit-Reset')
                if remaining == '0' and reset_time:
                    reset_timestamp = int(reset_time)
                    wait_time = reset_timestamp - time.time() + 5
                    if wait_time > 0:
                        print(f"  ⏳ Rate limited! Waiting {wait_time:.0f} seconds...")
                        time.sleep(wait_time)
                        # Retry the request
                        response = requests.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                # Content is base64 encoded
                content = base64.b64decode(data['content']).decode('utf-8')
                return self.truncate_content(content)
            elif response.status_code == 404:
                # README not found via API, try other methods
                pass
            else:
                print(f"  ⚠️  API returned status {response.status_code}")
        except Exception as e:
            print(f"  ⚠️  API error: {e}")
        
        # 2. Try common filename patterns (case insensitive)
        readme_patterns = [
            'README.md', 'readme.md', 'README.MD', 'Readme.md',
            'README.txt', 'README', 'readme',
            'README.rst', 'README.markdown', 'README.mdown',
            'README.adoc', 'README.asciidoc'
        ]
        
        branches = ['main', 'master', 'develop', 'dev', 'trunk']
        
        for branch in branches:
            for pattern in readme_patterns:
                url = f"https://raw.githubusercontent.com/{repo_name}/{branch}/{pattern}"
                try:
                    response = requests.get(url, headers={'User-Agent': 'RepoClassifier/1.0'}, timeout=10)
                    if response.status_code == 200:
                        return self.truncate_content(response.text)
                except:
                    continue
        
        # 3. Try to find via GitHub HTML (look for README link)
        try:
            html_url = f"https://github.com/{repo_name}"
            response = requests.get(html_url, headers={'User-Agent': 'RepoClassifier/1.0'}, timeout=10)
            if response.status_code == 200:
                # Look for README link in the HTML
                readme_links = re.findall(r'href="[^"]*?/blob/[^"]*?/([^"]*?\.md)"', response.text)
                if readme_links:
                    # Try the first few found
                    branch_match = re.search(r'/blob/([^/]+)/', response.text)
                    if branch_match:
                        branch = branch_match.group(1)
                        for link in readme_links[:3]:  # Try first 3 found
                            url = f"https://raw.githubusercontent.com/{repo_name}/{branch}/{link}"
                            try:
                                response2 = requests.get(url, headers={'User-Agent': 'RepoClassifier/1.0'}, timeout=10)
                                if response2.status_code == 200:
                                    return self.truncate_content(response2.text)
                            except:
                                continue
        except:
            pass
        
        return None
    
    def parse_classification_from_text(self, text):
        """Simple parser - looks for JSON first, then keywords"""
        if not text:
            return 'needs_review', ''
        
        # FIRST: Try to find JSON
        json_match = re.search(r'\{[^{}]*\}', text)
        if json_match:
            try:
                data = json.loads(json_match.group())
                classification = data.get('classification', '').lower().strip()
                if classification in ['app', 'framework', 'needs_review']:
                    return classification, data.get('reasoning', text[:300])
            except:
                pass
        
        # SECOND: Look for keywords
        text_lower = text.lower()
        
        # Check for 'needs_review' indicators first
        if any(word in text_lower for word in ['needs review', 'needs_review', 'unclear', 'ambiguous']):
            return 'needs_review', text[:300]
        
        # Look for 'framework' or 'app'
        has_framework = 'framework' in text_lower
        has_app = 'app' in text_lower or 'application' in text_lower
        
        # If both are present, check which one comes last (often the conclusion)
        if has_framework and has_app:
            last_framework = text_lower.rfind('framework')
            last_app = max(text_lower.rfind('app'), text_lower.rfind('application'))
            if last_framework > last_app:
                return 'framework', text[:300]
            else:
                return 'app', text[:300]
        
        # Only one is present
        if has_framework:
            return 'framework', text[:300]
        if has_app:
            return 'app', text[:300]
        
        # Neither found
        return 'needs_review', text[:300]
    
    def classify_url(self, url: str, readme_content: str) -> Dict[str, Any]:
        """
        Classify a single URL with improved parsing
        
        Args:
            url: GitHub URL
            readme_content: README content
            
        Returns:
            Classification result
        """
        # Truncate README to avoid token limits
        readme_content = self.truncate_content(readme_content, 5000) if readme_content else "README not available"
        
        # Start a fresh chat session for each URL
        self.client.start_chat()
        
        # Create prompt with README content
        prompt = f"""
Is this repository primarily an agentic application (a system that uses agents to solve
a specific problem) or an agent framework (a library for building agentic applications)? Answer with a single label.
If you are unsure, respond with "needs_review". Provide a brief reasoning for your classification.

Repo: {url}
README:
{readme_content}

Reply with JSON: {{"classification": "app|framework|needs_review", "reasoning": "brief"}}
"""
        
        try:
            # Send prompt to GPT
            response = self.client.send_prompt(prompt)
            response_text = self.client.response_to_text(response)
            
            # Parse the JSON response
            classification, reasoning = self.parse_classification_from_text(response_text)
            
            return {
                'url': url,
                'classification': classification,
                'reasoning': reasoning,
                'raw_response': response_text,
                'error': None,
                'processed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'url': url,
                'classification': 'needs_review',
                'reasoning': f"Error: {str(e)}",
                'raw_response': '',
                'error': str(e),
                'processed_at': datetime.now().isoformat()
            }
    
    def save_results(self, results, output_file):
        """Save results to JSON file with counts"""
        # Update counts before saving
        self.update_counts(results)
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        
        # Create backup
        if os.path.exists(output_file):
            backup_file = f"{output_file}.backup"
            try:
                os.rename(output_file, backup_file)
                if os.path.exists(f"{output_file}.backup.old"):
                    os.remove(f"{output_file}.backup.old")
                os.rename(backup_file, f"{output_file}.backup")
            except:
                pass
        
        # Save results with counts
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        # Remove old backup
        if os.path.exists(f"{output_file}.backup"):
            try:
                os.rename(f"{output_file}.backup", f"{output_file}.backup.old")
            except:
                pass
    
    def load_existing_results(self, output_file):
        """Load existing results if they exist"""
        if os.path.exists(output_file):
            try:
                with open(output_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return None
        return None
    
    def classify_urls(self, urls: List[str], output_file: str = None, resume: bool = False) -> List[Dict[str, Any]]:
        """
        Classify a list of URLs using GPT - with improved error handling and memory optimization
        
        Args:
            urls: List of GitHub URLs to classify
            output_file: Path to save classifications (optional)
            resume: Whether to resume from existing file
            
        Returns:
            List of classification results
        """
        # Create output file path if not provided
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(CLASSIFICATIONS_DIR, f"classifications_gpt_{timestamp}.json")
        
        self.results_file = output_file
        processed_urls = set()
        
        # Check GitHub rate limit before starting
        self.check_rate_limit()
        
        # Try to load existing results
        existing_results = None
        if resume:
            existing_results = self.load_existing_results(output_file)
            if existing_results:
                print(f"🔄 Resuming from existing results: {output_file}")
                for result in existing_results.get('results', []):
                    processed_urls.add(result.get('url'))
                print(f"   Found {len(processed_urls)} already processed URLs")
        
        # Initialize results
        if existing_results:
            results = existing_results
            results['total_urls'] = len(urls)
        else:
            results = {
                'model': f"gpt-{self.model_name}",
                'processed_at': datetime.now().isoformat(),
                'total_urls': len(urls),
                'counts': {
                    'app': 0,
                    'framework': 0,
                    'needs_review': 0,
                    'error': 0
                },
                'successful': 0,
                'failed': 0,
                'results': []
            }
        
        self.current_results = results
        
        # Set up signal handler
        signal.signal(signal.SIGINT, self.signal_handler)
        
        print(f"\n📊 Processing {len(urls)} repositories with GPT-{self.model_name}...")
        print(f"💾 Results will be saved incrementally to: {output_file}")
        print("=" * 60)
        print(f"{'Progress':<10} {'URL':<50} {'Classification':<15}")
        print("-" * 75)
        
        # Memory optimization: Save every 10 results and clear from memory
        SAVE_INTERVAL = 10
        results_since_last_save = 0
        
        # Rate limiting tracking
        request_count = 0
        api_call_count = 0
        last_rate_check = time.time()
        RATE_CHECK_INTERVAL = 60  # Check rate limit every 60 seconds
        
        for idx, url in enumerate(urls, 1):
            # Skip if already processed
            if url in processed_urls:
                print(f"\n[{idx}/{len(urls)}] ⏭️  Skipping already processed: {url}")
                continue
            
            print(f"\n[{idx}/{len(urls)}] {url[:50]}...")
            
            # Check rate limit periodically
            current_time = time.time()
            if current_time - last_rate_check > RATE_CHECK_INTERVAL:
                remaining = self.check_rate_limit()
                last_rate_check = current_time
                
                # If rate limit is low, wait
                if remaining is not None and remaining < 10:
                    wait_time = 60  # Wait 1 minute
                    print(f"⏳ Rate limit low ({remaining} remaining). Waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    # Check again after waiting
                    self.check_rate_limit()
            
            if 'github.com' not in url:
                print("  ⏭️  Skipping non-GitHub URL")
                result_entry = {
                    'url': url,
                    'error': 'Non-GitHub URL',
                    'classification': None,
                    'reasoning': None,
                    'processed_at': datetime.now().isoformat()
                }
                results['results'].append(result_entry)
                results['failed'] += 1
                self.current_results = results
                results_since_last_save += 1
                if results_since_last_save >= SAVE_INTERVAL:
                    self.save_results(results, output_file)
                    results_since_last_save = 0
                    gc.collect()
                continue
            
            # Fetch README with retry logic
            print("  📥 Fetching README...")
            readme = None
            retry_count = 0
            max_retries = 3
            
            while retry_count < max_retries and readme is None:
                try:
                    readme = self.get_readme_content(url)
                    if readme is None:
                        retry_count += 1
                        if retry_count < max_retries:
                            wait_time = 2 ** retry_count * 5  # Exponential backoff: 5, 10, 20 seconds
                            print(f"  ⏳ Fetch failed. Retry {retry_count}/{max_retries} in {wait_time}s...")
                            time.sleep(wait_time)
                    else:
                        break
                except Exception as e:
                    print(f"  ❌ Fetch error: {e}")
                    retry_count += 1
                    if retry_count < max_retries:
                        time.sleep(5)
            
            if not readme:
                print("  ❌ Could not fetch README after retries")
                result_entry = {
                    'url': url,
                    'error': 'README not found after retries',
                    'classification': None,
                    'reasoning': None,
                    'processed_at': datetime.now().isoformat()
                }
                results['results'].append(result_entry)
                results['failed'] += 1
                self.current_results = results
                results_since_last_save += 1
                if results_since_last_save >= SAVE_INTERVAL:
                    self.save_results(results, output_file)
                    results_since_last_save = 0
                    gc.collect()
                
                # Add extra delay after failure to avoid hammering API
                time.sleep(2)
                continue
            
            print(f"  ✅ README fetched ({len(readme)} chars)")
            
            print("  🤖 Classifying with GPT...")
            try:
                result_entry = self.classify_url(url, readme)
                
                results['results'].append(result_entry)
                if result_entry.get('error'):
                    results['failed'] += 1
                else:
                    results['successful'] += 1
                self.current_results = results
                
                classification = result_entry.get('classification', 'unknown')
                print(f"     ✅ {classification}")
            except Exception as e:
                print(f"     ❌ Error: {e}")
                result_entry = {
                    'url': url,
                    'error': str(e),
                    'classification': None,
                    'reasoning': None,
                    'processed_at': datetime.now().isoformat()
                }
                results['results'].append(result_entry)
                results['failed'] += 1
                self.current_results = results
            
            # Update counts
            self.update_counts(results)
            results_since_last_save += 1
            request_count += 1
            api_call_count += 1
            
            # Save every N results to free memory
            if results_since_last_save >= SAVE_INTERVAL:
                self.save_results(results, output_file)
                results_since_last_save = 0
                print(f"     💾 Saved and cleared from memory")
                gc.collect()
            
            # Show current counts
            print(f"     📊 Counts: App={self.counts['app']}, Framework={self.counts['framework']}, Review={self.counts['needs_review']}, Errors={self.counts['error']}")
            
            # Adaptive delay based on request count
            if api_call_count % 10 == 0:  # Every 10 API calls
                # Longer delay to respect rate limits
                delay = 5
                print(f"     ⏳ Rate limit pause: {delay}s delay after {api_call_count} calls")
                time.sleep(delay)
            else:
                # Standard delay between requests
                time.sleep(1)
        
        # Final save with counts
        self.update_counts(results)
        self.save_results(results, output_file)
        
        print(f"\n✅ COMPLETE! Final results saved to: {output_file}")
        return results
    
    def process_urls_from_file(self, input_file: str, output_file: str = None, resume: bool = False) -> str:
        """
        Process URLs from a file and save classifications incrementally
        
        Args:
            input_file: Path to file containing URLs (one per line)
            output_file: Path to save classifications (optional)
            resume: Whether to resume from existing file
            
        Returns:
            Path to the output file
        """
        print(f"📖 Reading URLs from: {input_file}")
        
        # Read URLs from file
        with open(input_file, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
        
        print(f"📊 Found {len(urls)} URLs to classify")
        
        # Create output file path if not provided
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(CLASSIFICATIONS_DIR, f"classifications_gpt_{timestamp}.json")
        
        # Classify URLs with incremental saving
        results = self.classify_urls(urls, output_file, resume)
        
        return output_file
    
    def process_urls_from_list(self, urls: List[str], output_file: str = None, resume: bool = False) -> str:
        """
        Process a list of URLs and save classifications incrementally
        
        Args:
            urls: List of URLs to classify
            output_file: Path to save classifications (optional)
            resume: Whether to resume from existing file
            
        Returns:
            Path to the output file
        """
        print(f"📊 Processing {len(urls)} URLs")
        
        # Create output file path if not provided
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(CLASSIFICATIONS_DIR, f"classifications_gpt_{timestamp}.json")
        
        # Classify URLs with incremental saving
        results = self.classify_urls(urls, output_file, resume)
        
        return output_file

def compare_with_other_classifiers(gpt_file: str, other_files: List[str] = None):
    """
    Compare GPT classifications with other classifiers
    
    Args:
        gpt_file: Path to GPT classification file
        other_files: List of paths to other classification files
    """
    print("\n🔍 Comparing GPT classifications with other classifiers")
    print("=" * 60)
    
    # Load GPT classifications
    try:
        with open(gpt_file, 'r', encoding='utf-8') as f:
            gpt_data = json.load(f)
    except FileNotFoundError:
        print(f"❌ File not found: {gpt_file}")
        return
    
    gpt_results = {r['url']: r.get('classification', '') for r in gpt_data.get('results', [])}
    
    # Find other files if not provided
    if other_files is None:
        other_files = glob.glob('joined_*_classifications.json')
        other_files = [f for f in other_files if f != gpt_file]
    
    if not other_files:
        print("❌ No other classification files found to compare")
        return
    
    print(f"\n📊 Comparing with {len(other_files)} other classifiers")
    print("-" * 60)
    
    for other_file in other_files:
        try:
            with open(other_file, 'r', encoding='utf-8') as f:
                other_data = json.load(f)
            
            model_name = other_data.get('model', os.path.basename(other_file))
            other_results = {r['url']: r.get('classification', '') for r in other_data.get('results', [])}
            
            # Find common URLs
            common_urls = set(gpt_results.keys()) & set(other_results.keys())
            
            if common_urls:
                agreements = sum(1 for url in common_urls if gpt_results[url] == other_results[url])
                disagreements = len(common_urls) - agreements
                
                print(f"\n🤖 vs {model_name}:")
                print(f"  - Common URLs: {len(common_urls)}")
                print(f"  - Agree: {agreements} ({agreements/len(common_urls)*100:.1f}%)")
                print(f"  - Disagree: {disagreements} ({disagreements/len(common_urls)*100:.1f}%)")
        except Exception as e:
            print(f"⚠️ Error reading {other_file}: {e}")

def main():
    """Main function to run GPT classification"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Classify GitHub repositories using GPT')
    parser.add_argument('--model', '-m', default='gpt-5.4-mini',
                       help='GPT model to use (gpt-5.4-mini, gpt-4, etc.)')
    parser.add_argument('--input', '-i', default='urls_to_classify.txt',
                       help='Input file with URLs (one per line)')
    parser.add_argument('--output', '-o',
                       help='Output JSON file (auto-generated if not specified)')
    parser.add_argument('--limit', '-l', type=int,
                       help='Limit number of URLs to process')
    parser.add_argument('--resume', action='store_true',
                       help='Resume from existing output file')
    
    args = parser.parse_args()
    
    # Initialize classifier with your model
    classifier = URLClassifier(model=args.model)
    
    if os.path.exists(args.input):
        print(f"📂 Found URLs file: {args.input}")
        urls = []
        with open(args.input, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
    else:
        print(f"⚠️ File '{args.input}' not found!")
        print("📝 Using sample URLs instead:")
        urls = [
            "https://github.com/forgeai-dev/ForgeAI",
            "https://github.com/genieincodebottle/multi-agents-app-on-aws",
            "https://github.com/aniket-work/autonomous-rfp-agent",
            "https://github.com/langchain-ai/langchain",
            "https://github.com/microsoft/markitdown",
        ]
    
    if args.limit and args.limit < len(urls):
        urls = urls[:args.limit]
        print(f"📊 Limited to {args.limit} URLs")
    
    print(f"📊 Total URLs to process: {len(urls)}")
    
    # Process URLs from list
    output_file = classifier.process_urls_from_list(urls, args.output, resume=args.resume)
    
    # Final summary
    print("\n" + "=" * 60)
    print(f"FINAL SUMMARY - GPT-{args.model}")
    print("=" * 60)
    
    # Load final results
    with open(output_file, 'r', encoding='utf-8') as f:
        results = json.load(f)
    
    print(f"Total URLs: {results['total_urls']}")
    print(f"Successful: {results['successful']}")
    print(f"Failed: {results['failed']}")
    print(f"Results saved to: {output_file}")
    
    # Show counts from the results
    counts = results.get('counts', {})
    print("\nClassification breakdown:")
    print(f"  App: {counts.get('app', 0)}")
    print(f"  Framework: {counts.get('framework', 0)}")
    print(f"  Needs Review: {counts.get('needs_review', 0)}")
    print(f"  Errors: {counts.get('error', 0)}")
    
    # Show percentages
    total = results['total_urls']
    if total > 0:
        print(f"\nPercentages:")
        print(f"  App: {(counts.get('app', 0) / total * 100):.1f}%")
        print(f"  Framework: {(counts.get('framework', 0) / total * 100):.1f}%")
        print(f"  Needs Review: {(counts.get('needs_review', 0) / total * 100):.1f}%")
        print(f"  Errors: {(counts.get('error', 0) / total * 100):.1f}%")
    
    # Compare with other classifiers if available
    compare_with_other_classifiers(output_file)

if __name__ == "__main__":
    main()