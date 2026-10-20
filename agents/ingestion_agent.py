"""
Ingestion Agent — reads the raw CSV dataset, cleans each row, converts it to a
KBArticle, builds a rich text document, and stores it in ChromaDB.

Why this approach:
- Each support ticket becomes one KB article (the "raw KB" that will be audited).
- We build a rich text document so ChromaDB's embedding captures all fields.
- Upsert is idempotent: re-running won't duplicate articles.
"""
from __future__ import annotations
import csv
import logging
import uuid
from datetime import date
from pathlib import Path

from config import settings
from models import KBArticle
from tools.vector_store import upsert_articles, collection_count
from tools.audit_log import append_entry

logger = logging.getLogger(__name__)

AGENT_NAME = "ingestion_agent"


def _row_to_article(row: dict) -> KBArticle:
    """Convert a raw CSV row to a validated KBArticle."""
    return KBArticle(
        article_id=row["ticket_id"],
        title=f"{row['category']} — {row['product']}",
        category=row["category"],
        subcategory=row["subcategory"],
        product=row["product"],
        severity=row["severity"],
        summary=row["summary"].strip(),
        resolution=row["resolution_notes"].strip(),
        created_date=date.fromisoformat(row["created_date"]),
        resolved_date=date.fromisoformat(row["resolved_date"]),
        resolution_time_hrs=float(row["resolution_time_hrs"]),
        sla_met=row["sla_met"].strip().lower() == "true",
        is_repetitive=row["is_repetitive"].strip().lower() == "true",
        tags=[row["category"], row["product"], row["severity"]],
        status="active",
    )


def _article_to_document(article: KBArticle) -> str:
    """
    Build a rich text string for embedding.
    Packing all semantic fields so similarity search works well.
    """
    return (
        f"Title: {article.title}\n"
        f"Category: {article.category} | Subcategory: {article.subcategory}\n"
        f"Product: {article.product} | Severity: {article.severity}\n"
        f"Summary: {article.summary}\n"
        f"Resolution: {article.resolution}\n"
        f"SLA Met: {article.sla_met} | Resolution Time: {article.resolution_time_hrs}h\n"
        f"Tags: {', '.join(article.tags)}"
    )


def run(data_path: str | None = None, limit: int | None = None) -> dict:
    """
    Main entry point called by the Orchestrator.
    Reads the CSV, ingests all rows into ChromaDB.
    Returns a summary dict for the audit log.
    """
    path = Path(data_path or settings.kb_data_path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    logger.info("[Ingestion] Loading %s", path)

    articles: list[KBArticle] = []
    errors: list[str] = []

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit and i >= limit:
                break
            try:
                articles.append(_row_to_article(row))
            except Exception as e:
                errors.append(f"{row.get('ticket_id', i)}: {e}")
                logger.warning("[Ingestion] Skipped row %d: %s", i, e)

    if not articles:
        raise RuntimeError("No articles parsed from dataset.")

    # Prepare ChromaDB batch
    ids = [a.article_id for a in articles]
    documents = [_article_to_document(a) for a in articles]
    metadatas = [
        {
            "category": a.category,
            "subcategory": a.subcategory,
            "product": a.product,
            "severity": a.severity,
            "sla_met": str(a.sla_met),
            "is_repetitive": str(a.is_repetitive),
            "resolution_time_hrs": a.resolution_time_hrs,
            "status": a.status,
            "created_date": a.created_date.isoformat(),
            "resolved_date": a.resolved_date.isoformat(),
        }
        for a in articles
    ]

    total_stored = upsert_articles(ids, documents, metadatas)

    summary = {
        "articles_parsed": len(articles),
        "errors": len(errors),
        "total_in_store": total_stored,
        "error_details": errors[:5],  # cap for log readability
    }

    # Write to audit log — proof of agent action
    append_entry(
        agent=AGENT_NAME,
        action="ingest_csv",
        inputs={"data_path": str(path), "limit": limit},
        outputs=summary,
        sources=ids[:10],  # sample of IDs
        outcome="success" if not errors else "partial",
        notes=f"Ingested {len(articles)} articles from {path.name}",
    )

    logger.info("[Ingestion] Done: %s", summary)
    return summary
