#!/usr/bin/env python3
"""
Configuration for GitHub Repo Crawler
"""
from frameworks import Frameworks

# Framework detector
frameworks_manager = Frameworks()

# Language requirements
REQUIRED_LANGUAGES = ['Python', 'JavaScript', 'TypeScript']
MIN_LANGUAGE_PERCENTAGE = 70.0  # Minimum % of Python/JS/TS required

# Search settings
RESULTS_PER_PAGE = 100
MAX_PAGES_PER_QUERY = 10
DEFAULT_TARGET_REPOS = 100

# Languages to search (will be added to query)
SEARCH_LANGUAGES = ['python', 'javascript', 'typescript']

# Query groups - just the search terms
SEARCH_QUERY_GROUPS = {
    "agent_frameworks": [
        "agent",
        "agentic app",
        "llm agent",
        "autonomous agent",
        "intelligent agent",
    ],
    "specific_frameworks": [
        "langchain",
        "autogen",
        "crewai",
        "llamaindex",
        "haystack",
        "mastra",
        "elizaos",
        "copilotkit",
    ],
    "multi_agent": [
        "multi-agent",
        "multi-agent system",
        "multi-agent framework",
        "agent orchestration",
        "agent swarm"
    ],
    "comprehensive": [
        "agent",
        "llm agent",
        "autonomous agent",
        "intelligent agent",
        "multi-agent system",
        "langchain",
        "autogen",
        "crewai",
        "llamaindex",
        "haystack",
        "mastra",
        "elizaos",
        "copilotkit",
    ],
    "high_value": [
        "agent framework stars:>100",
        "llm agent stars:>100",
        "autonomous agent stars:>100",
        "langchain stars:>100",
        "autogen stars:>100",
    ],
}

# File paths
MASTER_FILE = 'all_repos.json'
SEARCHED_URLS_FILE = 'searched_urls.json'
CACHE_FILE = 'seen_repos.json'