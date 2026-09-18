# RAG Creation Patterns

Scope: restricted to frameworks listed in Table II of the RADAR paper (Memory & RAG
category, plus LlamaIndex which sits in Orchestration but owns the dominant RAG-index
class). This file was previously empty; built now because `named_stores` detection
(feeding `shared_across_agents`, `rag_writers`, `rag_readers`) depends on it.

## Detection Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `detect_rag_creation(code, language)` | Detects vectorstore/memory-store instantiation | Dictionary with store details |
| `count_rag_stores(code, language)` | Counts distinct store instantiations | Integer count |
| `is_rag_framework(code, language)` | Quick boolean check for RAG framework usage | True/False |

---

## Python RAG Creation Patterns

| Framework | Detection Patterns |
|-----------|-------------------|
| **Chroma** | `Chroma(`<br>`chromadb.Client(`<br>`chromadb.PersistentClient(`<br>`Chroma.from_documents(`<br>`Chroma.from_texts(` |
| **Pinecone** | `Pinecone(`<br>`pinecone.Index(`<br>`Pinecone.from_existing_index(`<br>`Pinecone.from_documents(`<br>`Pinecone.from_texts(` |
| **Qdrant** | `QdrantClient(`<br>`Qdrant.from_documents(`<br>`Qdrant.from_texts(` |
| **Weaviate** | `weaviate.Client(`<br>`weaviate.connect_to_local(`<br>`weaviate.connect_to_weaviate_cloud(`<br>`Weaviate.from_documents(`<br>`Weaviate.from_texts(` |
| **PGVector** | `PGVector(` |
| **Milvus** | `MilvusClient(`<br>`connections.connect(`<br>`Milvus.from_documents(` |
| **Mem0** | `Memory(`<br>`MemoryClient(` |
| **Zep** | `ZepClient(`<br>`AsyncZep(`<br>`Zep(` |
| **LlamaIndex** | `VectorStoreIndex(`<br>`VectorStoreIndex.from_documents(`<br>`VectorStoreIndex.from_vector_store(`<br>`SimpleDirectoryReader(` |

---

## JavaScript/TypeScript RAG Creation Patterns

| Framework | Detection Patterns |
|-----------|-------------------|
| **Chroma JS** | `new ChromaClient(` |
| **Pinecone JS** | `new Pinecone(` |
| **Qdrant JS** | `new QdrantClient(` |
| **Weaviate JS** | `weaviate.client(` |
| **Zep JS** | `new ZepClient(` |

---

## Generic RAG Creation Patterns (Framework-Agnostic)

Checked for redundancy against the framework rows above — unlike most other
generic sections in this project, every entry here is genuinely necessary:
none of these are literally listed under any specific framework's row, so
removing any of them would create a real detection gap, not just save a line.

| Pattern Type | Regex Pattern | Description |
|-------------|---------------|--------------|
| Classmethod Factory | `\.from_documents\s*\(`<br>`\.from_texts\s*\(`<br>`\.from_vector_store\s*\(`<br>`\.from_existing_index\s*\(` | Vectorstore built via a classmethod factory rather than a direct constructor — very common in real RAG code |
| Generic Client | `\bClient\s*\(`<br>`\bPersistentClient\s*\(` | Ambiguous bare client constructors; only meaningful once corroborated by an import or a framework-specific pattern elsewhere in the file |