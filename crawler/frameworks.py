"""
Framework definitions and categories for agentic applications
Filtered for JS/TS and Python only frameworks
"""

class Frameworks:
    # Framework categories based on their primary purpose
    CATEGORIES = {
        "agent_orchestration": "Agent Orchestration & Multi-Agent Systems",
        "memory_rag": "Memory & RAG (Retrieval-Augmented Generation)",
        "tool_use": "Tool Use & Browser Automation",
        "llm_sdks": "LLM SDKs & Function Calling",
        "protocols": "Integration Protocols (MCP/A2A)"
    }
    
    FRAMEWORKS = {
        # Agent Orchestration & Multi-Agent Systems
        "LangChain": {
            "keywords": ["langchain", "@langchain/core", "langchain-core", "langchain4j"],
            "dependencies": ["langchain", "@langchain/core", "langchain-core"],
            "category": "agent_orchestration"
        },
        "LangGraph": {
            "keywords": ["langgraph", "@langchain/langgraph", "langgraph.js"],
            "dependencies": ["langgraph", "@langchain/langgraph"],
            "category": "agent_orchestration"
        },
        "LlamaIndex": {
            "keywords": ["llama-index", "llama_index", "llamaindex", "llama-index-core"],
            "dependencies": ["llama-index-core", "llamaindex"],
            "category": "agent_orchestration"
        },
        "CrewAI": {
            "keywords": ["crewai", "crew-ai", "crew_ai"],
            "dependencies": ["crewai"],
            "category": "agent_orchestration"
        },
        "AutoGen": {
            "keywords": ["pyautogen", "autogen", "autogen-agentchat", "autogen-agent-chat"],
            "dependencies": ["pyautogen", "autogen-agentchat"],
            "category": "agent_orchestration"
        },
        "Mastra": {
            "keywords": ["mastra", "@mastra/core", "mastra.ai"],
            "dependencies": ["@mastra/core", "mastra"],
            "category": "agent_orchestration"
        },
        "Vercel AI SDK": {
            "keywords": ["ai-sdk", "@ai-sdk", "vercel-ai", "ai/react"],
            "dependencies": ["ai", "@ai-sdk/openai", "@ai-sdk/react"],
            "category": "agent_orchestration"
        },
        "ElizaOS": {
            "keywords": ["elizaos", "@elizaos/core", "eliza-os"],
            "dependencies": ["@elizaos/core"],
            "category": "agent_orchestration"
        },
        "Bee Agent Framework": {
            "keywords": ["bee-agent", "bee-agent-framework", "bee_agent"],
            "dependencies": ["bee-agent-framework"],
            "category": "agent_orchestration"
        },
        "Smolagents": {
            "keywords": ["smolagents", "smol-agents"],
            "dependencies": ["smolagents"],
            "category": "agent_orchestration"
        },
        "Pydantic AI": {
            "keywords": ["pydantic-ai", "pydantic_ai"],
            "dependencies": ["pydantic-ai"],
            "category": "agent_orchestration"
        },
        "Agno": {
            "keywords": ["agno", "agno-framework"],
            "dependencies": ["agno"],
            "category": "agent_orchestration"
        },
        "Haystack": {
            "keywords": ["haystack", "haystack-ai"],
            "dependencies": ["haystack-ai"],
            "category": "agent_orchestration"
        },
        "OpenAI Agents SDK": {
            "keywords": ["@openai/agents", "openai-agents", "agents-sdk"],
            "dependencies": ["@openai/agents", "openai-agents-sdk"],
            "category": "agent_orchestration"
        },
        "Deep Agents JS": {
            "keywords": ["deep-agents", "@langchain/deep-agents", "deepagents"],
            "dependencies": ["deepagents", "@langchain/deep-agents", "deep-agents"],
            "category": "agent_orchestration"
        },
        
        # LLM SDKs & Function Calling
        "OpenAI SDK": {
            "keywords": ["openai", "openai-sdk"],
            "dependencies": ["openai"],
            "category": "llm_sdks"
        },
        "Anthropic SDK": {
            "keywords": ["anthropic", "@anthropic-ai/sdk", "anthropic-java"],
            "dependencies": ["anthropic", "@anthropic-ai/sdk"],
            "category": "llm_sdks"
        },
        "Google GenAI": {
            "keywords": ["google-genai", "@google/genai", "google-cloud-vertexai"],
            "dependencies": ["google-genai", "@google/genai", "google-cloud-vertexai"],
            "category": "llm_sdks"
        },
        "Together SDK": {
            "keywords": ["together", "together-ai", "together-sdk", "@together-ai/sdk", "together-ai-sdk"],
            "dependencies": ["together", "together-ai", "@together-ai/sdk", "together-sdk"],
            "category": "llm_sdks"
        },
        "Instructor": {
            "keywords": ["instructor", "@instructor-ai/instructor", "instructor-go"],
            "dependencies": ["instructor", "@instructor-ai/instructor"],
            "category": "llm_sdks"
        },
        "js-agent": {
            "keywords": ["js-agent", "@lgrammel/js-agent"],
            "dependencies": ["js-agent", "@lgrammel/js-agent"],
            "category": "llm_sdks"
        },
        "CopilotKit": {
            "keywords": ["copilotkit", "@copilotkit/react", "copilot-kit"],
            "dependencies": ["copilotkit", "@copilotkit/react-core"],
            "category": "llm_sdks"
        },
        
        # Memory & RAG
        "Mem0": {
            "keywords": ["mem0", "mem0ai"],
            "dependencies": ["mem0ai"],
            "category": "memory_rag"
        },
        "Chroma": {
            "keywords": ["chroma", "chromadb"],
            "dependencies": ["chromadb"],
            "category": "memory_rag"
        },
        "Weaviate": {
            "keywords": ["weaviate", "weaviate-client"],
            "dependencies": ["weaviate-client", "weaviate-ts-client"],
            "category": "memory_rag"
        },
        "Qdrant": {
            "keywords": ["qdrant", "qdrant-client"],
            "dependencies": ["qdrant-client", "@qdrant/js-client-rest"],
            "category": "memory_rag"
        },
        "Pinecone": {
            "keywords": ["pinecone", "pinecone-client"],
            "dependencies": ["pinecone-client", "@pinecone-database/pinecone"],
            "category": "memory_rag"
        },
        "Zep": {
            "keywords": ["zep", "zep-python"],
            "dependencies": ["zep-python", "@getzep/zep-js"],
            "category": "memory_rag"
        },
        "PGVector": {
            "keywords": ["pgvector", "pgvector-python"],
            "dependencies": ["pgvector"],
            "category": "memory_rag"
        },
        "Milvus": {
            "keywords": ["milvus", "pymilvus"],
            "dependencies": ["pymilvus", "@zilliz/milvus2-sdk-node"],
            "category": "memory_rag"
        },
        
        # Tool Use & Browser Automation
        "Playwright": {
            "keywords": ["playwright"],
            "dependencies": ["playwright"],
            "category": "tool_use"
        },
        "Browser-use": {
            "keywords": ["browser-use", "browseruse"],
            "dependencies": ["browser-use"],
            "category": "tool_use"
        },
        "Puppeteer": {
            "keywords": ["puppeteer"],
            "dependencies": ["puppeteer"],
            "category": "tool_use"
        },
        "Stagehand": {
            "keywords": ["stagehand", "@browserbasehq/stagehand"],
            "dependencies": ["@browserbasehq/stagehand"],
            "category": "tool_use"
        },
        "E2B": {
            "keywords": ["e2b"],
            "dependencies": ["e2b"],
            "category": "tool_use"
        },
        "Composio": {
            "keywords": ["composio", "composio-core"],
            "dependencies": ["composio-core"],
            "category": "tool_use"
        },
        
        # Integration Protocols
        "MCP SDK": {
            "keywords": ["mcp", "@modelcontextprotocol/sdk", "model-context-protocol"],
            "dependencies": ["mcp", "@modelcontextprotocol/sdk"],
            "category": "protocols"
        },
        "FastMCP": {
            "keywords": ["fastmcp"],
            "dependencies": ["fastmcp"],
            "category": "protocols"
        },
        "A2A SDK": {
            "keywords": ["a2a-sdk", "a2a_sdk"],
            "dependencies": ["a2a-sdk"],
            "category": "protocols"
        }
    }
    
    def __init__(self):
        pass

    def get_frameworks(self):
        """Return all registered frameworks."""
        return self.FRAMEWORKS
    
    def get_categories(self):
        """Return all categories with their display names."""
        return self.CATEGORIES
    
    def get_frameworks_by_category(self, category):
        """Get all frameworks in a specific category."""
        return {
            name: info for name, info in self.FRAMEWORKS.items()
            if info.get("category") == category
        }
    
    def get_category_for_framework(self, framework_name):
        """Get the category for a specific framework."""
        if framework_name in self.FRAMEWORKS:
            return self.FRAMEWORKS[framework_name].get("category")
        return None
    
    def get_category_display_name(self, category_key):
        """Get display name for a category key."""
        return self.CATEGORIES.get(category_key, category_key)
    
    def is_in_frameworks(self, framework_name):
        """Check if a framework name is registered."""
        return framework_name in self.FRAMEWORKS
    
    def get_framework_by_dependency(self, dependency_name):
        """Find framework by dependency package name."""
        for framework_name, framework_info in self.FRAMEWORKS.items():
            if dependency_name in framework_info["dependencies"]:
                return framework_name
        return None
    
    def detect_frameworks_from_dependencies(self, dependencies, source_type='package.json'):
        """
        Detect frameworks from a set of dependencies.
        
        Args:
            dependencies: Set or dict of dependency names
            source_type: Type of dependency file ('package.json' or 'requirements.txt')
        
        Returns:
            List of detected frameworks with their info
        """
        detected = []
        
        for framework_name, framework_info in self.FRAMEWORKS.items():
            for dep in framework_info["dependencies"]:
                if isinstance(dependencies, dict):
                    if dep in dependencies:
                        detected.append({
                            'name': framework_name,
                            'category': framework_info["category"],
                            'category_display': self.CATEGORIES.get(framework_info["category"], framework_info["category"])
                        })
                        break
                else:  # set or list
                    if dep in dependencies:
                        detected.append({
                            'name': framework_name,
                            'category': framework_info["category"],
                            'category_display': self.CATEGORIES.get(framework_info["category"], framework_info["category"])
                        })
                        break
        
        return detected
    
    def detect_frameworks_from_text(self, text):
        """
        Detect frameworks from text content (like README).
        
        Args:
            text: Text content to search for keywords
        
        Returns:
            List of detected frameworks with their info
        """
        detected = []
        text_lower = text.lower()
        
        for framework_name, framework_info in self.FRAMEWORKS.items():
            for keyword in framework_info["keywords"]:
                if keyword.lower() in text_lower:
                    detected.append({
                        'name': framework_name,
                        'category': framework_info["category"],
                        'category_display': self.CATEGORIES.get(framework_info["category"], framework_info["category"])
                    })
                    break
        
        return detected
    
    def get_all_keywords(self):
        """Get all keywords across all frameworks."""
        keywords = {}
        for framework_name, framework_info in self.FRAMEWORKS.items():
            for keyword in framework_info["keywords"]:
                if keyword not in keywords:
                    keywords[keyword] = []
                keywords[keyword].append(framework_name)
        return keywords
    
    def get_all_dependencies(self):
        """Get all unique dependencies across all frameworks."""
        dependencies = set()
        for framework_info in self.FRAMEWORKS.values():
            dependencies.update(framework_info["dependencies"])
        return sorted(list(dependencies))