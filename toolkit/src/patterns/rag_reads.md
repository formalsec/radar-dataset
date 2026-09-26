# RAG Reads Patterns

Scope: restricted to frameworks listed in Table II of the RADAR paper. Mirrors the
structure of `rag_writes.md` so the two can be diffed field-for-field when computing
`rag_readers` / `rag_writers` in the metadata schema.

Note: several read signatures (`.query(`, `.search(`, `.run(`) overlap with write or
agent-call signatures elsewhere in this pipeline. Apply the same confirmation-gating
logic used for framework detection (Section IV-B / pattern_scan) before counting a bare
`.query(` or `.search(` as a memory read — require a preceding binding to a known
vector-store/memory constructor in the same scope.

## Detection Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `detect_rag_reads(code, language)` | Detects RAG read/retrieval operations | Dictionary with read details |
| `count_document_queries(code, language)` | Counts retrieval/query calls | Integer count |
| `detect_retriever_wiring(code, language)` | Detects a retriever object wired into an agent/chain | List of retriever bindings |

---

## Python RAG Read Patterns

| Framework | Read Patterns |
|-----------|---------------|
| **Chroma** | `.query(`<br>`.get(` |
| **Pinecone** | `.query(`<br>`.fetch(` |
| **Qdrant** | `.search(`<br>`.query_points(`<br>`.scroll(` |
| **Weaviate** | `.query.near_text(`<br>`.query.fetch_objects(`<br>`.query.near_vector(` |
| **PGVector** | `.similarity_search(`<br>`.similarity_search_with_score(` |
| **Milvus** | `.search(`<br>`.query(` |
| **Zep** | `.memory.get(`<br>`.search_memory(`<br>`.asearch_memory(`<br>`.thread.get_user_context(` |
| **LangChain** | `.similarity_search(`<br>`.as_retriever(`<br>`.get_relevant_documents(` |
| **LlamaIndex** | `.as_retriever(`<br>`.retrieve(`<br>`query_engine.query(` |
| **Mem0** | `.search(`<br>`.get_all(` |
| **Agno** | `knowledge.search(` |
| **Haystack** | retriever component `.run(` *(disambiguate from write via preceding "Retriever(" constructor)* |

---

## JavaScript/TypeScript RAG Read Patterns

| Framework | Read Patterns |
|-----------|---------------|
| **Chroma JS** | `.query({` |
| **Pinecone JS** | `.query({`<br>`.fetch({` |
| **Qdrant JS** | `.search(`<br>`.query(` |
| **Weaviate JS** | `.query.nearText(`<br>`.query.fetchObjects(` |
| **Zep JS** | `.memory.get(`<br>`.searchMemory(` |
| **LangChain JS** | `.similaritySearch(`<br>`.asRetriever(` |

---

## Agent-Specific Read Patterns

| Framework | Read Patterns |
|-----------|---------------|
| **CrewAI** | `collection.query(`<br>`vector_store.similarity_search(` |
| **AutoGen** | `memory.get(`<br>`memory.query(` |
| **LangGraph** | `vector_store.similarity_search(`<br>`retriever_node(` |
| **Agno** | `knowledge.search(`<br>`knowledge.retrieve(` |

---

## Generic RAG Read Patterns

Trimmed from 5 buckets to 3, and merged with the "Read Operation Types" table
that used to sit below this one (same redundant-second-table pattern as
`rag_writes.md`). Removed as redundant with a framework row above: `.query(`
(Chroma JS/Pinecone JS/Qdrant JS), `.similarity_search(` (CrewAI/LangGraph),
`.search(` (Qdrant JS), `.fetch(` (Pinecone JS), `.asRetriever(` (LangChain JS).

| Pattern Type | Regex Pattern | Description |
|-------------|---------------|-------------|
| Retriever Fetch | `\.retrieve\s*\(`<br>`\.get_relevant_documents\s*\(` | Fetching via a retriever object — not tied to any single framework's row |
| Search (memory) | `\.search_memory\s*\(` | Distinct from Zep's qualified `.memory.get(`, which is already covered |
| Retriever Wiring / Generic Fetch | `\.as_retriever\s*\(`<br>`\.get\s*\(` | Bare Python forms broader than any qualified framework row — `.get(` is intentionally low-confidence and needs context gating (overlaps with unrelated dict/object access) |

---

## API-Based Reads

| API Pattern | Description |
|-------------|-------------|
| `GET /api/memories` | Memory retrieval endpoint |
| `GET /api/memories/{id}` | Single memory retrieval endpoint |
| `POST /api/search` | Search-style retrieval endpoint (some vector-DB proxies expose search as POST) |
| `X-Agent-ID` | Agent identification header |