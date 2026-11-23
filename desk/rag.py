"""RAG over the policy docs.

Challenge 4 (naive, kept as the ablation baseline): fixed-size chunks, dense top-k.
Challenge 5 (advanced): heading-aware chunks with a [Title | Section | Effective] header, BM25 + dense hybrid
merged with Reciprocal Rank Fusion, top-20 -> top-5 feature rerank, follow-up query rewriting, and a
latest-effective_date preference when two policies give different rules for the same thing.
"""
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .llm import LLMError

CHUNK_CHARS = 2000  # ~500 tokens at ~4 chars/token
OVERLAP_CHARS = 200  # ~50 tokens
TOP_K = 4  # Ch4 naive retrieval
FINAL_K = 5  # Ch5: chunks given to the model
CANDIDATES = 20  # Ch5: hybrid candidates before the rerank
RRF_K = 60  # standard Reciprocal Rank Fusion constant
MAX_SECTION_CHARS = 2000  # longer sections are split between paragraphs (tables are never split)
# Word overlap at which two same-family sections count as rules about the same thing. On the seed policies the
# real conflict (returns.md#3 vs returns_opened_items.md#2) scores 0.43; the closest unrelated pair scores 0.30.
CONFLICT_OVERLAP = 0.35
RERANK_WEIGHTS = {"coverage": 0.5, "heading": 0.3, "sku": 1.0}
CHROMA_DIR = Path(".chroma")
COLLECTION = "policies"
REFUSAL = "not in the documents"
_ID = r"\.(?:md|csv)#[\w-]+"  # returns.md#3, returns.md#3-2 (part 2 of a long section), catalog.csv#MS-ELC0410
CITATION = re.compile(r"\[([^\[\]\s]+" + _ID + r")\]")
CHUNK_ID = re.compile(r"[^\s,;†\[\]【】]+" + _ID)
# Any [...] or 【...】 group holding chunk ids, e.g. 【returns.md#1】, 【warranty.md#1†L3-L5】, [a.md#1, b.md#2]
BRACKET_GROUP = re.compile(r"[\[【]([^\[\]【】\n]*?" + _ID + r"[^\[\]【】\n]*)[\]】]")
SKU = re.compile(r"\bMS-[A-Z]{3}\d{4}\b", re.I)
FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)
HEADING = re.compile(r"^##[ \t]+(\d+)\.?[ \t]*(.*)$", re.M)
STOPWORDS = set("""a an and are as at be by can do does did for from get got how i if in is it its me my of on or our
the to what when where which who whom why will with you your about after any there this that these those than then
much many please tell need should would could""".split())


@dataclass
class Hit:
    id: str  # "returns.md#2"
    text: str
    distance: float = None  # cosine distance from the dense search (None if only BM25 found it)
    score: float = None  # Ch5 fused / reranked score (None for Ch4 dense-only hits)
    meta: dict = field(default_factory=dict)
    note: str = ""  # e.g. which rule is newer, shown to the model above the chunk


# ---- Challenge 4: naive fixed-size chunks -----------------------------------

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


def policy_files(policy_dir):
    files = sorted(Path(policy_dir).glob("*.md"))
    if not files:
        raise FileNotFoundError(f"no .md files in {policy_dir}")
    return files


def load_chunks(policy_dir):
    """Every .md file in policy_dir -> (files, ids, documents, metadatas), chunk numbers 1-based per file."""
    files = policy_files(policy_dir)
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


def ingest(collection, policy_dir, loader=load_chunks):
    """Idempotent: deterministic ids + upsert, and chunks no longer produced by any file are deleted."""
    files, ids, docs, metas = loader(policy_dir)
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


# ---- Challenge 5: heading-aware chunks --------------------------------------

def parse_front_matter(text):
    """'---\\nkey: value\\n---' block at the top -> (dict, rest of the text)."""
    m = FRONT_MATTER.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, text[m.end():]


def split_blocks(body):
    """Paragraph blocks separated by blank lines; a Markdown table (consecutive '|' lines) is always one block."""
    blocks, cur, in_table = [], [], False
    for line in body.splitlines():
        is_table = line.lstrip().startswith("|")
        if not line.strip() or is_table != in_table:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        if line.strip():
            cur.append(line)
        in_table = is_table
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def pack_blocks(blocks, limit=MAX_SECTION_CHARS):
    """Greedily join blocks up to `limit` chars. Tables stay whole; an over-long paragraph is window-split."""
    parts, cur = [], ""
    for block in blocks:
        pieces = [block] if block.lstrip().startswith("|") or len(block) <= limit else chunk_text(block, limit)
        for piece in pieces:
            if cur and len(cur) + 2 + len(piece) > limit:
                parts.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        parts.append(cur)
    return parts


def chunk_markdown(text, filename):
    """One chunk per '## N. Heading' section (id file#N; long sections become file#N-1, file#N-2 ...).

    Each chunk starts with a '[Title | N. Heading | Effective YYYY-MM-DD]' header so the section keeps its
    context (and its date) when it is retrieved on its own. Text before the first '##' becomes file#0.
    """
    fm, body = parse_front_matter(text.strip() + "\n")
    h1 = re.search(r"^#[ \t]+(.+)$", body, re.M)
    title = fm.get("title") or (h1.group(1).strip() if h1 else Path(filename).stem)
    effective = fm.get("effective_date", "")
    marks = list(HEADING.finditer(body))
    preamble = re.sub(r"^#[ \t]+.*$", "", body[:marks[0].start()] if marks else body, flags=re.M).strip()
    sections = [("0", "Preamble" if marks else "Section 0", preamble)] if preamble else []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        sections.append((m.group(1), f"{m.group(1)}. {m.group(2).strip()}".strip(), body[m.end():end].strip()))
    out = []
    for num, heading, content in sections:
        parts = pack_blocks(split_blocks(content))
        for j, part in enumerate(parts, 1):
            cid = f"{filename}#{num}" if len(parts) == 1 else f"{filename}#{num}-{j}"
            header = f"[{title} | {heading} | Effective {effective or 'unknown'}]"
            meta = {"source": filename, "section": num, "title": title, "heading": heading,
                    "effective_date": effective, "doc_id": fm.get("doc_id", "")}
            out.append((cid, f"{header}\n{part}", meta))
    return out


def load_sections(policy_dir):
    """Like load_chunks, but heading-aware: (files, ids, documents, metadatas)."""
    files = policy_files(policy_dir)
    ids, docs, metas = [], [], []
    for f in files:
        for cid, doc, meta in chunk_markdown(f.read_text(encoding="utf-8"), f.name):
            ids.append(cid)
            docs.append(doc)
            metas.append(meta)
    return files, ids, docs, metas


# ---- Challenge 5: hybrid search, rerank, recency ----------------------------

def tokens(text):
    """Lower-case content words for BM25 / rerank: '18,000' -> '18000', SKUs kept whole, light plural stemming."""
    text = re.sub(r"(?<=\d),(?=\d)", "", text or "")
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
             for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]
    return words + [s.lower() for s in SKU.findall(text)]


def body_of(text):
    """Chunk text without its '[Title | Section | Effective]' header line."""
    first, _, rest = text.partition("\n")
    return rest if first.startswith("[") and first.endswith("]") else text


class Corpus:
    """Every chunk in a Chroma collection plus a BM25 index over them (rebuilt per process - the corpus is small)."""

    def __init__(self, collection):
        from rank_bm25 import BM25Okapi

        got = collection.get(include=["documents", "metadatas"])
        self.collection = collection
        self.ids = list(got["ids"])
        self.docs = dict(zip(self.ids, got["documents"]))
        self.metas = {i: dict(m or {}) for i, m in zip(self.ids, got["metadatas"] or [{}] * len(self.ids))}
        corpus = [tokens(self.docs[i]) for i in self.ids]
        self.bm25 = BM25Okapi(corpus) if any(corpus) else None

    def dense(self, query, n):
        """Chroma's embedding search -> (ids best first, {id: cosine distance})."""
        if not self.ids:
            return [], {}
        r = self.collection.query(query_texts=[query], n_results=min(n, len(self.ids)), include=["distances"])
        return r["ids"][0], dict(zip(r["ids"][0], r["distances"][0]))

    def keyword(self, query, n):
        """BM25 ranking (ids best first, only chunks sharing at least one query word)."""
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(tokens(query))
        order = sorted(range(len(self.ids)), key=lambda i: -scores[i])
        return [self.ids[i] for i in order[:n] if scores[i] > 0]

    def is_stale(self):
        """True if the index was built by the Ch4 ingest (no section metadata)."""
        return any("heading" not in m for m in self.metas.values())


def rrf(rankings, k=RRF_K):
    """Reciprocal Rank Fusion: score(id) = sum over rankings of 1 / (k + rank). Best first."""
    scores = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def rerank_score(query, text, heading, base):
    """Cheap cross-features between the query and one chunk, added to the fused score (scaled 0-1).

    coverage: share of query words found in the chunk; heading: share found in the section title;
    sku: the query names an exact SKU (MS-ABC1234) that appears in the chunk.
    """
    q = set(tokens(query))
    if not q:
        return base
    coverage = len(q & set(tokens(text))) / len(q)
    in_heading = len(q & set(tokens(heading))) / len(q)
    skus = {s.upper() for s in SKU.findall(query)}
    sku = 1.0 if skus and any(s in text.upper() for s in skus) else 0.0
    w = RERANK_WEIGHTS
    return base + w["coverage"] * coverage + w["heading"] * in_heading + w["sku"] * sku


def family(meta):
    """Policy family from the doc id: POL-RET-001 and POL-RET-002 are both 'POL-RET'."""
    doc_id = meta.get("doc_id") or ""
    return doc_id.rsplit("-", 1)[0] if "-" in doc_id else meta.get("source", "")


def word_overlap(a, b):
    ta, tb = set(tokens(body_of(a))), set(tokens(body_of(b)))
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def supersedes(new, old):
    """True if `new` is a later-dated rule about the same thing as `old` (same policy family, other document)."""
    n, o = new.meta, old.meta
    return (n.get("source") != o.get("source") and family(n) == family(o) and bool(n.get("effective_date"))
            and bool(o.get("effective_date")) and n["effective_date"] > o["effective_date"]
            and word_overlap(new.text, old.text) >= CONFLICT_OVERLAP)


def prefer_latest(ranked, k=FINAL_K):
    """Top-k, but when an older rule has a newer version, the newer one is pulled in and ranked just above it.

    Both chunks get a NOTE naming the dates; if the list is full, the lowest-ranked chunk makes room.
    """
    final, pairs = list(ranked[:k]), []
    for old in list(final):
        new = next((h for h in ranked if supersedes(h, old)), None)
        if new is None or old not in final:
            continue
        if new not in final:
            final.insert(final.index(old), new)
            final = final[:k]
        elif final.index(new) > final.index(old):  # only ever move the newer rule up
            final.remove(new)
            final.insert(final.index(old), new)
        pairs.append((new, old))
    for new, old in pairs:
        if old in final:
            old.note = (f"OLDER rule, effective {old.meta['effective_date']} - replaced by {new.id} "
                        f"(effective {new.meta['effective_date']})")
    for new in {id(n): n for n, _ in pairs}.values():
        replaced = ", ".join(f"{o.id} (effective {o.meta['effective_date']})" for n, o in pairs if n is new)
        new.note = f"NEWER rule, effective {new.meta['effective_date']} - replaces {replaced}"
    return final


@dataclass
class Pipeline:
    hybrid: bool = True  # BM25 + dense via RRF (else dense only)
    rerank: bool = True  # feature rerank of the candidates
    recency: bool = True  # latest effective_date preference
    k: int = FINAL_K
    candidates: int = CANDIDATES


def search(corpus, query, p=None):
    """Dense (+ BM25) top-20 -> RRF -> (rerank) -> top-5 -> (latest-date preference). Returns Hits, best first."""
    p = p or Pipeline()
    dense_ids, distances = corpus.dense(query, p.candidates)
    rankings = [dense_ids] + ([corpus.keyword(query, p.candidates)] if p.hybrid else [])
    fused = rrf(rankings)[:p.candidates]
    top = fused[0][1] if fused else 1.0
    scored = [(cid, s / top) for cid, s in fused]
    if p.rerank:
        scored = sorted(((cid, rerank_score(query, corpus.docs[cid],
                                            f"{corpus.metas[cid].get('title', '')} {corpus.metas[cid].get('heading', '')}", s))
                         for cid, s in scored), key=lambda kv: -kv[1])
    ranked = [Hit(cid, corpus.docs[cid], distances.get(cid), round(s, 4), corpus.metas[cid]) for cid, s in scored]
    return prefer_latest(ranked, p.k) if p.recency else ranked[:p.k]


def rewrite_query(question, history, complete):
    """Turn a follow-up ('and for 30,000?') into a standalone query using the conversation.

    `complete(system, user) -> str` is one LLM call. No history -> the question unchanged. If the LLM fails,
    fall back to the previous user question + the follow-up.
    """
    from .prompts import REWRITE_PROMPT

    if not history:
        return question
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    try:
        rewritten = complete(REWRITE_PROMPT, f"Conversation so far:\n{convo}\n\nFollow-up: {question}")
    except LLMError:
        rewritten = ""
    rewritten = (rewritten or "").strip().strip("\"'").strip()
    if rewritten:
        return rewritten.splitlines()[0]
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    return f"{last_user} {question}".strip()


def catalog_hits(text, catalog_path):
    """Stretch: catalog.csv rows for every SKU named in `text`, as extra context chunks (catalog.csv#SKU)."""
    skus = {s.upper() for s in SKU.findall(text)}
    if not skus or not Path(catalog_path).is_file():
        return []
    hits = []
    with open(catalog_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("sku", "").upper() in skus:
                body = "\n".join(f"{k}: {v}" for k, v in row.items())
                hits.append(Hit(f"catalog.csv#{row['sku']}", f"[Product catalog | {row['sku']}]\n{body}",
                                meta={"source": "catalog.csv"}))
    return hits


# ---- answer formatting ------------------------------------------------------

def build_context(question, hits):
    """User message: the retrieved chunks, each labelled with the id the model must cite, then the question."""
    blocks = "\n\n".join(f"[{h.id}]" + (f"\nNOTE: {h.note}" if h.note else "") + f"\n{h.text}" for h in hits)
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


def debug_line(h):
    """One --debug line per retrieved chunk."""
    if h.score is None and h.distance is None:
        return f"[{h.id}] catalog lookup | {snippet(body_of(h.text))}"
    if h.score is None:  # Ch4 dense-only hit
        return f"[{h.id}] distance {h.distance:.3f} (similarity {1 - h.distance:.3f}) | {snippet(h.text)}"
    dense = f"distance {h.distance:.3f}" if h.distance is not None else "BM25 only"
    note = f" | {h.note}" if h.note else ""
    return f"[{h.id}] score {h.score:.3f} ({dense}){note} | {snippet(body_of(h.text))}"
