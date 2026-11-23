"""Challenge 4: naive RAG over the policy docs - fixed-size chunks, local Chroma index, top-k retrieval."""
import re
from dataclasses import dataclass
from pathlib import Path

CHUNK_CHARS = 2000  # ~500 tokens at ~4 chars/token
OVERLAP_CHARS = 200  # ~50 tokens
TOP_K = 4
CHROMA_DIR = Path(".chroma")
COLLECTION = "policies"
REFUSAL = "not in the documents"
CITATION = re.compile(r"\[([^\[\]\s]+\.md#\d+)\]")
CHUNK_ID = re.compile(r"[^\s,;†\[\]【】]+\.md#\d+")
# Any [...] or 【...】 group holding chunk ids, e.g. 【returns.md#1】, 【warranty.md#1†L3-L5】, [a.md#1, b.md#2]
BRACKET_GROUP = re.compile(r"[\[【]([^\[\]【】\n]*?\.md#\d+[^\[\]【】\n]*)[\]】]")


@dataclass
class Hit:
    id: str  # "returns.md#2"
    text: str
    distance: float  # cosine distance: 0 = identical, similarity = 1 - distance


def chunk_text(text, size=CHUNK_CHARS, overlap=OVERLAP_CHARS):
    """Fixed-size character windows; each starts `overlap` chars before the previous one ended."""
    if not 0 <= overlap < size:
        raise ValueError("overlap must be >= 0 and smaller than size")
    text = text.strip()
    chunks, start = [], 0
    while text:
        chunks.append(text[start:start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


def load_chunks(policy_dir):
    """Every .md file in policy_dir -> (ids, documents, metadatas), chunk numbers 1-based per file."""
    files = sorted(Path(policy_dir).glob("*.md"))
    if not files:
        raise FileNotFoundError(f"no .md files in {policy_dir}")
    ids, docs, metas = [], [], []
    for f in files:
        for i, chunk in enumerate(chunk_text(f.read_text(encoding="utf-8")), 1):
            ids.append(f"{f.name}#{i}")
            docs.append(chunk)
            metas.append({"source": f.name, "chunk": i})
    return files, ids, docs, metas


def open_collection(path=CHROMA_DIR):
    """Persistent local Chroma collection using Chroma's built-in local embedding model (no API key)."""
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(COLLECTION, configuration={"hnsw": {"space": "cosine"}})


def ingest(collection, policy_dir):
    """Idempotent: deterministic ids + upsert, and chunks no longer produced by any file are deleted."""
    files, ids, docs, metas = load_chunks(policy_dir)
    existing = set(collection.get(include=[])["ids"])
    stale = sorted(existing - set(ids))
    if stale:
        collection.delete(ids=stale)
    collection.upsert(ids=ids, documents=docs, metadatas=metas)
    return {"files": len(files), "chunks": len(ids), "new": len(set(ids) - existing),
            "removed": len(stale), "total": collection.count()}


def retrieve(collection, question, k=TOP_K):
    r = collection.query(query_texts=[question], n_results=k, include=["documents", "distances"])
    return [Hit(i, d, dist) for i, d, dist in zip(r["ids"][0], r["documents"][0], r["distances"][0])]


def build_context(question, hits):
    """User message: the retrieved chunks, each labelled with the id the model must cite, then the question."""
    blocks = "\n\n".join(f"[{h.id}]\n{h.text}" for h in hits)
    return f"Context chunks:\n\n{blocks}\n\nQuestion: {question}"


def normalize_citations(answer):
    """Rewrite gpt-oss style 【returns.md#1】 (and multi-id groups) into the required [returns.md#1][...] form."""
    return BRACKET_GROUP.sub(lambda m: "".join(f"[{i}]" for i in CHUNK_ID.findall(m.group(1))), answer)


def is_refusal(answer):
    return answer.strip().strip("\"'`*.!").strip().lower() == REFUSAL


def citation_problems(answer, hits):
    """Empty list if the answer is the refusal or cites only retrieved chunks; else what is wrong."""
    if is_refusal(answer):
        return []
    cited = CITATION.findall(answer)
    if not cited:
        return ["answer has no [file.md#N] citations"]
    unknown = sorted(set(cited) - {h.id for h in hits})
    return [f"cites chunks that were not retrieved: {', '.join(unknown)}"] if unknown else []


def snippet(text, n=90):
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[:n - 3] + "..."
