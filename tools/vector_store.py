"""
Vector store tool — wraps ChromaDB for embedding storage and similarity search.
Used by: Ingestion Agent (write), Audit Agent (duplicate search), Q&A Agent (retrieval).
"""
from __future__ import annotations
import logging
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from config import settings

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "kb_articles"
_client: chromadb.Client | None = None
_collection: chromadb.Collection | None = None


def _get_collection() -> chromadb.Collection:
    """Return (or init) the persistent ChromaDB collection."""
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # get_or_create so re-runs don't fail
        _collection = _client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB collection '%s' ready (%d docs)", _COLLECTION_NAME, _collection.count())
    return _collection


def upsert_articles(
    ids: list[str],
    documents: list[str],       # the text to embed
    metadatas: list[dict],
) -> int:
    """
    Embed and store KB articles.
    ChromaDB handles embedding via its default (sentence-transformers/all-MiniLM-L6-v2).
    Returns the number of items now in the collection.
    """
    col = _get_collection()
    # batch upsert — safe to call multiple times (idempotent)
    col.upsert(ids=ids, documents=documents, metadatas=metadatas)
    count = col.count()
    logger.info("Upserted %d articles; total in store: %d", len(ids), count)
    return count


def similarity_search(
    query: str,
    n_results: int = 5,
    where: Optional[dict] = None,
) -> list[dict]:
    """
    Find the n most similar articles to a query string.
    Returns list of dicts with keys: id, document, metadata, distance.
    """
    col = _get_collection()
    kwargs: dict = {"query_texts": [query], "n_results": min(n_results, col.count() or 1)}
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)
    # Flatten the nested lists ChromaDB returns
    out = []
    for i, doc_id in enumerate(results["ids"][0]):
        out.append({
            "id": doc_id,
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return out


def get_article(article_id: str) -> Optional[dict]:
    """Fetch a single article by ID."""
    col = _get_collection()
    res = col.get(ids=[article_id], include=["documents", "metadatas"])
    if not res["ids"]:
        return None
    return {
        "id": res["ids"][0],
        "document": res["documents"][0],
        "metadata": res["metadatas"][0],
    }


def get_all_articles(limit: int = 500) -> list[dict]:
    """Return all stored articles (capped at limit for safety)."""
    col = _get_collection()
    total = col.count()
    if total == 0:
        return []
    res = col.get(limit=min(limit, total), include=["documents", "metadatas"])
    return [
        {"id": rid, "document": doc, "metadata": meta}
        for rid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"])
    ]


def collection_count() -> int:
    return _get_collection().count()


def reset_collection() -> None:
    """Wipe and recreate the collection — for fresh demo runs."""
    global _collection
    if _client:
        _client.delete_collection(_COLLECTION_NAME)
        _collection = None
    logger.warning("ChromaDB collection reset.")
