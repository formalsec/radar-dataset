# RAG Writes Patterns

Scope: restricted to frameworks listed in Table II of the RADAR paper. "Husk" has been
removed from the JS/TS section (not in Table II). Zep has been added (Memory & RAG
category, previously missing).

## Detection Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `detect_rag_writes(code, language)` | Detects RAG write operations | Dictionary with write details |
| `count_document_inserts(code, language)` | Counts document insertions | Integer count |
| `detect_batch_writes(code, language)` | Detects batch write operations | List of batch operations |

---

## Python RAG Write Patterns

| Framework | Write Patterns |
|-----------|---------------|
| **Chroma** | `.add(`<br>`.upsert(`<br>`.update(` |
| **Pinecone** | `.upsert_records(`<br>`.update(` |
| **Qdrant** | `.upsert(`<br>`.upload_records(` |
| **Weaviate** | `.add_object(`<br>`.insertMany(` |
| **PGVector** | `.add_documents(` |
| **Milvus** | `.insert(` |
| **Zep** | `.memory.add(`<br>`.thread.add_messages(`<br>`.add_memory(`<br>`.aadd_memory(` *(legacy `zep-python` client)* |
| **LangChain** | `.add_documents(` |
| **LlamaIndex** | `.persist()`<br>`.insert(` |
| **Mem0** | `.add(`<br>`.store_conversation(` |
| **Agno** | `Knowledge()`<br>`knowledge.add_documents(` |
| **Haystack** | `.add_documents(`<br>`.run(` |

---

## JavaScript/TypeScript RAG Write Patterns

| Framework | Write Patterns |
|-----------|---------------|
| **Chroma JS** | `.add({`<br>`.upsert({` |
| **Pinecone JS** | `.upsertRecords(`<br>`.update(` |
| **Qdrant JS** | `.upsert(`<br>`.uploadRecords(`<br>`.upload_collection(` |
| **Weaviate JS** | `.insertMany(` |
| **Zep JS** | `.memory.add(`<br>`.addMemory(` |
| **LangChain JS** | `.addDocuments(` |

---

## Agent-Specific Write Patterns

| Framework | Write Patterns |
|-----------|---------------|
| **CrewAI** | `collection.add(`<br>`vector_store.add_documents(` |
| **AutoGen** | `memory.add(`<br>`memory.store(` |
| **LangGraph** | `vector_store.add_documents(`<br>`store_node(` |
| **Agno** | `knowledge.add_documents(`<br>`knowledge.add_texts(` |

---

## Generic RAG Write Patterns

Trimmed from 6 buckets to 3, and merged with the "Write Operation Types" table
that used to sit below this one — that table restated the same concepts under
a different header (adding only a Confidence column), and its one apparently
distinct entry (`.memory.add(`) turned out to be redundant with AutoGen's own
`memory.add(` row above anyway. One table now, not two.

Removed as redundant with a framework row above: `.add(`/`.add_documents(`/
`.addDocuments(` (Chroma/LangChain/PGVector/Haystack/Mem0/Agno already list
these), `.upsert(`/`.upsert_records(`/`.upsertRecords(` (Pinecone/Qdrant/
Chroma), `.store_conversation(` (Mem0), `.persist(` (LlamaIndex), `.update(`
(Chroma/Pinecone), `.upload_records(` (Qdrant).

| Pattern Type | Regex Pattern | Description |
|-------------|---------------|-------------|
| Batch Storage | `\.batch\.create\s*\(`<br>`batch\.add` | Batch operations — not tied to any single framework's row |
| Index Build | `\.build_index\s*\(` | Not tied to any single framework's row |
| Persistence (save) | `\.save\s*\(` | Persisting data — distinct from `.persist(`, which is already covered under LlamaIndex |
| Memory Storage | `\.store\s*\(`<br>`\.remember\s*\(` | Bare storage verbs broader than any single framework's qualified form (e.g. AutoGen's `memory.store(` is a narrower, already-covered case) |

---

## API-Based Writes

| API Pattern | Description |
|-------------|-------------|
| `POST /api/memories` | Memory creation endpoint |
| `POST /api/memories/` | Memory creation endpoint |
| `X-Agent-ID` | Agent identification header |