import time
import base64
import requests
from typing import Optional, Dict, Set, List, Tuple
from collections import Counter
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def create_session() -> requests.Session:
    """Create HTTP session with retry logic"""
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def fetch_url(session: requests.Session, url: str, headers: Dict, delay: float = 0.3) -> Optional[Dict]:
    """Make API request with rate limit handling"""
    time.sleep(delay)
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        
        # Handle rate limiting
        if response.status_code == 403:
            reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
            if reset_time:
                wait_time = max(reset_time - time.time(), 0) + 5
                print(f"\n⚠ Rate limit hit. Waiting {wait_time:.0f} seconds...")
                time.sleep(wait_time)
                return fetch_url(session, url, headers, delay)
        
        response.raise_for_status()
        return response.json()
        
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None

def fetch_file(session: requests.Session, headers: Dict, repo: str, path: str) -> Optional[str]:
    """Fetch a file from a repository"""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    data = fetch_url(session, url, headers)
    
    if data and 'content' in data:
        try:
            return base64.b64decode(data['content']).decode('utf-8', errors='ignore')
        except:
            pass
    return None

def get_repository_tree(session: requests.Session, headers: Dict, repo: str, recursive: bool = True) -> Optional[List]:
    """
    Get the complete file tree of a repository using GitHub's Git Trees API.
    This is much more efficient than fetching each file individually.
    
    Args:
        session: Requests session
        headers: Request headers
        repo: Repository name (format: "owner/repo")
        recursive: If True, get all files recursively
    
    Returns:
        List of tree items (files and directories) or None if error
    """
    # First, get the default branch
    repo_url = f"https://api.github.com/repos/{repo}"
    repo_data = fetch_url(session, repo_url, headers)
    
    if not repo_data:
        return None
    
    default_branch = repo_data.get('default_branch', 'main')
    
    # Get the tree SHA for the default branch
    if recursive:
        tree_url = f"https://api.github.com/repos/{repo}/git/trees/{default_branch}?recursive=1"
    else:
        tree_url = f"https://api.github.com/repos/{repo}/git/trees/{default_branch}"
    
    tree_data = fetch_url(session, tree_url, headers)
    
    if not tree_data or 'tree' not in tree_data:
        return None
    
    return tree_data['tree']

def count_python_js_ts_files(tree_items: List) -> Dict[str, int]:
    """
    Count only Python, JavaScript, and TypeScript files from tree items.
    
    Args:
        tree_items: List of tree items from GitHub API
    
    Returns:
        Dictionary with counts for python, javascript, typescript
    """
    # Define extensions for each language
    extensions = {
        'python': ['.py', '.pyw', '.pyx', '.pyi'],
        'javascript': ['.js', '.jsx', '.mjs', '.cjs'],
        'typescript': ['.ts', '.tsx']
    }
    
    counts = {
        'python': 0,
        'javascript': 0,
        'typescript': 0,
        'total_files': 0
    }
    
    for item in tree_items:
        if item['type'] != 'blob':  # Skip directories
            continue
        
        counts['total_files'] += 1
        file_path = item['path'].lower()
        
        # Check for Python files
        if any(file_path.endswith(ext) for ext in extensions['python']):
            counts['python'] += 1
        # Check for JavaScript files
        elif any(file_path.endswith(ext) for ext in extensions['javascript']):
            counts['javascript'] += 1
        # Check for TypeScript files
        elif any(file_path.endswith(ext) for ext in extensions['typescript']):
            counts['typescript'] += 1
    
    return counts

def parse_package_json(content: str) -> Set[str]:
    """Parse package.json and return set of dependencies"""
    import json
    try:
        data = json.loads(content)
        deps = set()
        deps.update(data.get('dependencies', {}).keys())
        deps.update(data.get('devDependencies', {}).keys())
        return deps
    except:
        return set()

def parse_requirements_txt(content: str) -> Set[str]:
    """Parse requirements.txt and return set of dependencies"""
    deps = set()
    for line in content.split('\n'):
        line = line.strip()
        if line and not line.startswith('#'):
            # Extract package name (remove version specifiers)
            package = line.split('=')[0].split('>')[0].split('<')[0].split('[')[0].strip()
            if package:
                deps.add(package)
    return deps

def calculate_language_percentage(languages: Dict) -> Dict[str, float]:
    """Convert byte counts to percentages"""
    if not languages:
        return {}
    
    total = sum(languages.values())
    return {lang: (bytes_count / total) * 100 for lang, bytes_count in languages.items()}

def get_all_languages_with_bytes(languages: Dict) -> List[Dict]:
    """Get all languages with their byte counts and percentages"""
    if not languages:
        return []
    
    total = sum(languages.values())
    result = []
    for lang, bytes_count in languages.items():
        percentage = (bytes_count / total) * 100
        result.append({
            'name': lang,
            'bytes': bytes_count,
            'percentage': round(percentage, 2)
        })
    
    # Sort by percentage descending
    result.sort(key=lambda x: x['percentage'], reverse=True)
    return result