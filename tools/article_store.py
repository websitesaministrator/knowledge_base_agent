"""
tools/article_store.py - Persistence interface for synthesized KB articles.
Local JSON storage behind an interface designed for direct drop-in replacement with Firestore.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

STORE_DIR = Path("data/articles")
STORE_FILE = Path("data/articles_store.json")


class ResolutionRecord(BaseModel):
    fix_id: int = Field(description="Standard fix ID number referenced in tickets")
    ticket_count: int = Field(default=1, description="Number of tickets resolved using this fix ID")
    other_categories_count: int = Field(default=0, description="Number of other distinct categories citing this fix ID")
    notes: str = Field(default="", description="Observations regarding fix distribution across categories")

    @classmethod
    def from_any(cls, item: Any) -> ResolutionRecord:
        if isinstance(item, cls):
            return item
        if isinstance(item, dict):
            return cls(**item)
        if isinstance(item, (int, str)):
            s = str(item)
            m = re.search(r"fix\s*#?(\d+)", s, re.IGNORECASE)
            fid = int(m.group(1)) if m else (int(s) if s.isdigit() else 1)
            return cls(fix_id=fid, ticket_count=1, notes=s)
        return cls(fix_id=1, ticket_count=1)


class SuggestedStep(BaseModel):
    text: str = Field(description="Actionable troubleshooting guidance")
    label: str = Field(default="AI-suggested, unverified", description="Must be labeled 'AI-suggested, unverified'")

    @classmethod
    def from_any(cls, item: Any) -> SuggestedStep:
        if isinstance(item, cls):
            return item
        if isinstance(item, str):
            return cls(text=item, label="AI-suggested, unverified")
        if isinstance(item, dict):
            text = item.get("text") or item.get("description") or item.get("step") or ""
            label = item.get("label") or item.get("note") or "AI-suggested, unverified"
            return cls(text=str(text), label=str(label))
        return cls(text=str(item), label="AI-suggested, unverified")


class ArticleEvidence(BaseModel):
    ticket_ids: list[str] = Field(default_factory=list, description="Verified ticket IDs from the cluster")
    ticket_count: int = Field(default=0, description="Total ticket count in the cluster")
    repetitive_rate: float = Field(default=0.0, description="Repetitive rate fraction (0.0 - 1.0)")

    @classmethod
    def from_any(cls, item: Any) -> ArticleEvidence:
        if isinstance(item, cls):
            return item
        if isinstance(item, dict):
            t_ids = item.get("ticket_ids") or item.get("ticket_id") or []
            if isinstance(t_ids, str):
                t_ids = [t_ids]
            t_count = item.get("ticket_count", len(t_ids))
            rep_rate = item.get("repetitive_rate", 0.0)
            return cls(ticket_ids=[str(x) for x in t_ids], ticket_count=int(t_count), repetitive_rate=float(rep_rate))
        return cls()


class KBArticleDraft(BaseModel):
    article_id: str = Field(description="Identifier in format 'KB-<cluster slug>-v<version>'")
    cluster_id: str = Field(description="Cluster in format '<category>|<subcategory>'")
    title: str = Field(description="Clear procedural article title")
    version: int = Field(default=1, description="Article version number")
    status: Literal["draft", "pending_review", "published", "flagged_stale", "retired"] = Field(
        default="draft", description="Lifecycle status"
    )
    what_we_see: str = Field(description="Factual symptoms, volume, repetitive rate, products, severity, citing tickets")
    resolutions_on_record: list[ResolutionRecord] = Field(description="Fix IDs with ticket counts and context")
    suggested_steps: list[SuggestedStep] = Field(description="Cautious steps, max 5, each labeled 'AI-suggested, unverified'")
    needs_sme_input: list[str] = Field(description="List of specific questions and facts a human SME must confirm")
    evidence: ArticleEvidence = Field(description="Ground truth ticket evidence")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def model_validate(cls, obj: Any, *args, **kwargs) -> KBArticleDraft:
        if isinstance(obj, dict):
            # Pre-process nested fields with from_any
            if "resolutions_on_record" in obj and isinstance(obj["resolutions_on_record"], list):
                obj["resolutions_on_record"] = [
                    ResolutionRecord.from_any(x).model_dump() for x in obj["resolutions_on_record"]
                ]
            if "suggested_steps" in obj and isinstance(obj["suggested_steps"], list):
                obj["suggested_steps"] = [
                    SuggestedStep.from_any(x).model_dump() for x in obj["suggested_steps"]
                ]
            if "evidence" in obj:
                obj["evidence"] = ArticleEvidence.from_any(obj["evidence"]).model_dump()
        return super().model_validate(obj, *args, **kwargs)


_memory_cache: dict[str, dict] = {}


def _init_store() -> None:
    """Load articles from disk into memory cache."""
    global _memory_cache
    if _memory_cache:
        return

    STORE_DIR.mkdir(parents=True, exist_ok=True)
    if STORE_FILE.exists():
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                _memory_cache = json.load(f)
        except Exception as e:
            logger.warning("Could not read %s, starting fresh: %s", STORE_FILE, e)
            _memory_cache = {}


def _persist() -> None:
    """Save in-memory articles to disk."""
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(_memory_cache, f, indent=2)


def save_article(article: dict | KBArticleDraft) -> dict:
    """
    Save or update an article draft in the store.
    Validates against KBArticleDraft schema and returns the stored article dict.
    """
    _init_store()

    if isinstance(article, dict):
        validated = KBArticleDraft.model_validate(article)
    else:
        validated = article

    art_dict = validated.model_dump()
    aid = art_dict["article_id"]
    _memory_cache[aid] = art_dict
    _persist()
    logger.info("Saved article %s (status=%s)", aid, art_dict["status"])
    return art_dict


def get_article(article_id: str) -> dict | None:
    """Retrieve an article by article_id, or None if not found."""
    _init_store()
    return _memory_cache.get(article_id)


def list_articles(status: str | None = None) -> list[dict]:
    """List all stored articles, optionally filtered by lifecycle status."""
    _init_store()
    articles = list(_memory_cache.values())
    if status:
        articles = [a for a in articles if a.get("status") == status]
    return articles


def cluster_to_slug(cluster_id: str) -> str:
    """Helper to convert 'Category|Subcategory - variant 1' into a clean URL-friendly slug."""
    slug = cluster_id.lower().replace("|", "-").replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug
