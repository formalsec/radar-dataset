import json
import os
import re
import time
import requests
from urllib.parse import urlparse
from datetime import datetime
import subprocess
import argparse
import signal
import sys
import gc
import base64

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLASSIFICATIONS_DIR = os.path.join(PROJECT_ROOT, 'crawler', 'output', 'llm_classifications')

class RepoClassifier:
    def __init__(self, ollama_url="http://localhost:11434", model="llama3.3"):
        self.ollama_url = ollama_url
        self.model = model
        self.results_file = None
        self.current_results = None
        # Add counters
        self.counts = {
            'app': 0,
            'framework': 0,
            'needs_review': 0,
            'error': 0
        }
        
        # Timeout settings - increased for Ollama
        self.TIMEOUTS = {
            'ollama_api': 5,        # Quick health check
            'ollama_generate': 300, # Increased to 300 seconds
            'github_api': 10,
            'github_raw': 15,
        }
        
    def signal_handler(self, sig, frame):
        """Handle Ctrl+C to save progress"""
        print("\n\n⚠️ Interrupted! Saving progress...")
        if self.current_results and self.results_file:
            self.update_counts(self.current_results)
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
        
        # Update the results object with counts
        results['counts'] = self.counts.copy()
    
    def truncate_content(self, content):
        """Helper to truncate content to avoid memory issues"""
        if not content:
            return content
        max_chars = 3000 if "deepseek" in self.model.lower() else 4000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [truncated]"
        return content
    
    def check_ollama(self):
        """Check if Ollama is running and model is available"""
        try:
            response = requests.get(
                f"{self.ollama_url}/api/tags",
                timeout=self.TIMEOUTS['ollama_api']
            )
            if response.status_code == 200:
                models = response.json().get('models', [])
                available_models = [m['name'] for m in models]
                print(f"Available Ollama models: {available_models}")
                
                if self.model not in available_models:
                    print(f"⚠️ Model '{self.model}' not found. Pulling it now...")
                    subprocess.run(['ollama', 'pull', self.model], check=True)
                
                # Warm up the model
                print(f"🔄 Warming up {self.model}...")
                try:
                    warmup_response = requests.post(
                        f"{self.ollama_url}/api/generate",
                        json={
                            "model": self.model,
                            "prompt": "Hello",
                            "max_tokens": 5,
                            "stream": False
                        },
                        timeout=30
                    )
                    if warmup_response.status_code == 200:
                        print(f"✅ {self.model} is ready")
                except:
                    print(f"⚠️ Warmup failed, but continuing...")
                
                return True
            return False
        except requests.exceptions.ConnectionError:
            print("❌ Ollama is not running. Please start it with: ollama serve")
            print("   Or with extended timeout: OLLAMA_LOAD_TIMEOUT=300 ollama serve")
            return False
        except Exception as e:
            print(f"❌ Error checking Ollama: {e}")
            return False
    
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
                    wait_time = reset - time.time() + 10
                    if wait_time > 0:
                        print(f"   Consider waiting {wait_time:.0f} seconds or use a GitHub token")
                
                return remaining
            else:
                print(f"⚠️  Could not check rate limit: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"⚠️  Error checking rate limit: {e}")
            return None
    
    def fetch_readme(self, repo_url):
        """Enhanced README fetching with more strategies and rate limit handling"""
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
            response = requests.get(
                api_url,
                headers=headers,
                timeout=self.TIMEOUTS['github_api']
            )
            
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
                        response = requests.get(
                            api_url,
                            headers=headers,
                            timeout=self.TIMEOUTS['github_api']
                        )
            
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
        except requests.exceptions.Timeout:
            print(f"  ⚠️ GitHub API timeout after {self.TIMEOUTS['github_api']}s")
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
                    response = requests.get(
                        url,
                        headers={'User-Agent': 'RepoClassifier/1.0'},
                        timeout=self.TIMEOUTS['github_raw']
                    )
                    if response.status_code == 200:
                        return self.truncate_content(response.text)
                except requests.exceptions.Timeout:
                    continue
                except:
                    continue
        
        # 3. Try to find via GitHub HTML (look for README link)
        try:
            html_url = f"https://github.com/{repo_name}"
            response = requests.get(
                html_url,
                headers={'User-Agent': 'RepoClassifier/1.0'},
                timeout=self.TIMEOUTS['github_raw']
            )
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
                                response2 = requests.get(
                                    url,
                                    headers={'User-Agent': 'RepoClassifier/1.0'},
                                    timeout=self.TIMEOUTS['github_raw']
                                )
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
    
    def classify_with_ollama(self, readme_content, repo_url, max_retries=3):
        """
        Send README to Ollama for classification with intelligent retry and timeout handling
        """
        prompt = f"""
Is this repository primarily an agentic application (a system that uses agents to solve
a specific problem) or an agent framework (a library for building agentic applications)? Answer with a single label.
If you are unsure, respond with "needs_review". Provide a brief reasoning for your classification.

Repo: {repo_url}
README:
{readme_content[:5000]}

Reply with JSON: {{"classification": "app|framework|needs_review", "reasoning": "brief"}}
"""
        
        # Try with increasing timeouts and retries
        for attempt in range(max_retries):
            # Calculate timeout with exponential backoff: 120, 180, 240 seconds
            timeout_value = 120 + (attempt * 60)
            
            try:
                if attempt > 0:
                    print(f"  🔄 Retry {attempt+1}/{max_retries} (timeout: {timeout_value}s)...")
                
                response = requests.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "max_tokens": 300,
                        "temperature": 0.2,
                        "stream": False,
                        "keep_alive": -1,  # Keep model loaded between requests
                        "options": {
                            "num_ctx": 2048,
                            "num_predict": 300
                        }
                    },
                    timeout=timeout_value,
                    headers={
                        'Content-Type': 'application/json',
                        'Connection': 'keep-alive'
                    }
                )

                if response.status_code == 200:
                    result = response.json()
                    response_text = result.get('response', '')
                    
                    # Try to parse JSON
                    json_match = re.search(r'\{[^{}]*\}', response_text)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                            classification = data.get('classification', '').lower().strip()
                            reasoning = data.get('reasoning', '')
                            if classification in ['app', 'framework', 'needs_review']:
                                return {
                                    'classification': classification,
                                    'reasoning': reasoning
                                }
                        except:
                            pass
                    
                    # Fallback: combine with thinking
                    full_text = response_text + " " + result.get('thinking', '')
                    classification, reasoning = self.parse_classification_from_text(full_text)
                    
                    return {
                        'classification': classification,
                        'reasoning': reasoning
                    }
                else:
                    print(f"  ⚠️ Ollama returned status {response.status_code}")
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        time.sleep(wait_time)
                        continue
                    
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"  ⏳ Ollama timeout ({timeout_value}s), retrying in {wait_time}s with longer timeout...")
                    time.sleep(wait_time)
                    
                    # If we're on the second retry, try to keep the model loaded
                    if attempt == 1:
                        try:
                            # Send a keepalive ping
                            requests.post(
                                f"{self.ollama_url}/api/generate",
                                json={
                                    "model": self.model,
                                    "prompt": " ",
                                    "max_tokens": 1,
                                    "stream": False
                                },
                                timeout=5
                            )
                        except:
                            pass
                    continue
                else:
                    print(f"  ❌ Ollama persistent timeout after {max_retries} attempts")
                    return {'classification': 'needs_review', 'reasoning': f'Timeout after {max_retries} retries'}
                    
            except requests.exceptions.ConnectionError:
                print("  ❌ Ollama connection error - is it running?")
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                return {'classification': 'needs_review', 'reasoning': 'Connection error'}
                
            except Exception as e:
                print(f"  ❌ Exception: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return {'classification': 'needs_review', 'reasoning': f'Error: {str(e)}'}
        
        return {'classification': 'needs_review', 'reasoning': 'Failed after retries'}

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
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
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
                with open(output_file, 'r') as f:
                    return json.load(f)
            except:
                return None
        return None
    
    def process_urls(self, urls, output_file=None, resume=False):
        """Process URLs with incremental saving, rate limiting, and memory optimization"""
        if not output_file:
            output_file = f"classifications_{self.model.replace(':', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
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
                'model': self.model,
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
        
        if not self.check_ollama():
            print("❌ Cannot proceed without Ollama")
            return results
        
        print(f"\n📊 Processing {len(urls)} repositories with {self.model}...")
        print(f"⏱️  Ollama timeout: {self.TIMEOUTS['ollama_generate']}s with retries")
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
                    readme = self.fetch_readme(url)
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
            
            print("  🤖 Classifying...")
            try:
                # Use the enhanced classify_with_ollama with retries
                result = self.classify_with_ollama(readme, url, max_retries=3)
                classification = result.get('classification', 'needs_review')
                reasoning = result.get('reasoning', '')
                
                result_entry = {
                    'url': url,
                    'classification': classification,
                    'reasoning': reasoning,
                    'error': None,
                    'processed_at': datetime.now().isoformat()
                }
                
                results['results'].append(result_entry)
                results['successful'] += 1
                self.current_results = results
                
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
            
            # Adaptive delay based on request count and rate limits
            if api_call_count % 10 == 0:  # Every 10 API calls
                # Longer delay to respect rate limits
                delay = 5
                print(f"     ⏳ Rate limit pause: {delay}s delay after {api_call_count} calls")
                time.sleep(delay)
            else:
                # Standard delay between requests
                # Add extra delay if we had a timeout/needs_review
                if classification == 'needs_review':
                    delay = 3
                else:
                    delay = 2 if "deepseek" in self.model.lower() else 1
                time.sleep(delay)
            
            # Clear readme from memory
            readme = None
        
        # Final save with counts
        self.update_counts(results)
        self.save_results(results, output_file)
        gc.collect()
        
        print(f"\n✅ COMPLETE! Final results saved to: {output_file}")
        return results
    
    def load_urls_from_file(self, file_path):
        """Load URLs from a text file"""
        with open(file_path, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
        return urls

def main():
    parser = argparse.ArgumentParser(description='Classify GitHub repositories using Ollama')
    parser.add_argument('--model', '-m', default='llama3.3',
                       help='Ollama model to use (llama3.3, deepseek-r1:7b, etc.)')
    parser.add_argument('--input', '-i', default='urls_to_classify.txt',
                       help='Input file with URLs (one per line)')
    parser.add_argument('--output', '-o',
                       help='Output JSON file (auto-generated if not specified)')
    parser.add_argument('--limit', '-l', type=int,
                       help='Limit number of URLs to process')
    parser.add_argument('--resume', action='store_true',
                       help='Resume from existing output file')
    parser.add_argument('--timeout', '-t', type=int, default=300,
                       help='Timeout in seconds per request (default: 300)')
    
    args = parser.parse_args()
    
    classifier = RepoClassifier(model=args.model)
    
    # Override timeout if specified
    if args.timeout:
        classifier.TIMEOUTS['ollama_generate'] = args.timeout
    
    if os.path.exists(args.input):
        print(f"Loading URLs from {args.input}")
        urls = classifier.load_urls_from_file(args.input)
    else:
        print(f"❌ File {args.input} not found!")
        print("Using sample URLs instead...")
        urls = [
            "https://github.com/langchain-ai/langchain",
            "https://github.com/microsoft/markitdown",
        ]
    
    if args.limit and args.limit < len(urls):
        urls = urls[:args.limit]
        print(f"Limited to {args.limit} URLs")
    
    print(f"Total URLs to process: {len(urls)}")
    print(f"Ollama timeout: {classifier.TIMEOUTS['ollama_generate']}s")
    
    if not args.output:
        args.output = os.path.join(
            CLASSIFICATIONS_DIR,
            f"classifications_{args.model.replace(':', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
    
    results = classifier.process_urls(urls, args.output, resume=args.resume)
    
    # Final summary with counts
    print("\n" + "=" * 60)
    print(f"FINAL SUMMARY - {args.model}")
    print("=" * 60)
    print(f"Total URLs: {results['total_urls']}")
    print(f"Successful: {results['successful']}")
    print(f"Failed: {results['failed']}")
    print(f"Results saved to: {args.output}")
    
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

if __name__ == "__main__":
    main()