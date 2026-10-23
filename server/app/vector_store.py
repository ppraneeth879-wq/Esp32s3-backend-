"""Vector store for semantic search (ChromaDB with in-memory fallback)."""

from __future__ import annotations

from typing import Optional

try:
    import chromadb
    from chromadb.utils import embedding_functions
    _HAS_CHROMA = True
except Exception:  # chroma not installed (e.g. lightweight env)
    _HAS_CHROMA = False


class _MemoryStore:
    """Tiny fallback vector store (keyword overlap)."""

    def __init__(self):
        self.items = []

    def add(self, vid, text, metadata=None):
        self.items.append({"id": vid, "text": text, "metadata": metadata or {}})

    def search(self, query, threshold=0.0, top_k=5):
        q = set(query.lower().split())
        scored = []
        for it in self.items:
            t = set(it["text"].lower().split())
            score = len(q & t) / len(q | t) if q and t else 0.0
            if score >= threshold:
                scored.append({"text": it["text"], "metadata": it["metadata"], "similarity": score, "id": it["id"]})
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]


class ChromaVectorStore:
    def __init__(self, path: str = "data/chromadb", collection: str = "memories"):
        self.path = path
        self.collection_name = collection
        self._client = None
        self._collection = None
        self._fallback = _MemoryStore()
        self._ready = False          # marks first init attempt done
        self._init_error = None

    def _initialize(self):
        """Initialize Chroma (downloading embedding model lazily on first query).
        Runs once; any failure leaves _collection=None so search uses the fallback."""
        if self._ready:
            return
        self._ready = True
        if not _HAS_CHROMA:
            return
        try:
            import os
            os.makedirs(self.path, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self.path)
            self._collection = self._client.get_or_create_collection(name=self.collection_name)
        except Exception as e:
            self._init_error = e
            self._collection = None

    def _lazy(self):
        if self._collection is None and not self._ready:
            self._initialize()
        if self._collection is None and self._ready and self._init_error is None:
            # First init succeeded but collection is still None (edge case)
            pass

    def add(self, vid: str, text: str, metadata: Optional[dict] = None) -> None:
        self._initialize()
        if self._collection is not None:
            try:
                self._collection.add(ids=[vid], documents=[text], metadatas=[metadata or {}])
            except Exception:
                self._fallback.add(vid, text, metadata)
        else:
            self._fallback.add(vid, text, metadata)

    def search(self, query: str, threshold: float = 0.0, top_k: int = 5) -> list[dict]:
        self._initialize()
        if self._collection is not None:
            try:
                res = self._collection.query(
                    query_texts=[query], n_results=top_k,
                    include=["documents", "metadatas", "distances"],
                )
                docs = res.get("documents", [[]])[0]
                metas = res.get("metadatas", [[]])[0]
                dists = res.get("distances", [[]])[0]
                out = []
                for d, m, dist in zip(docs or [], metas or [], dists or []):
                    out.append({"text": d, "metadata": m or {}, "similarity": 1 - (dist or 0)})
                return out
            except Exception:
                pass
        # Fallback: keyword overlap in-memory
        return self._fallback.search(query, threshold, top_k)

    def count(self) -> int:
        """How many vectors are stored (for diagnostics)."""
        self._initialize()
        if self._collection is not None:
            try:
                return len(self._collection.get(include=[])["ids"])
            except Exception:
                return 0
        return len(self._fallback.items)

    def reset(self) -> None:
        """Remove every vector from the collection.

        ChromaDB rejects a delete with no ids and no filters, so the ids are
        fetched first and passed explicitly.
        """
        self._initialize()
        if self._collection is not None:
            try:
                existing = self._collection.get(include=[])
                ids = existing.get("ids") or []
                if ids:
                    self._collection.delete(ids=ids)
            except Exception:
                pass
        self._fallback = _MemoryStore()


vector_store = ChromaVectorStore()
