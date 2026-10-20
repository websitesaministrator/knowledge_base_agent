"""
Structured data models shared across all agents.
Using Pydantic v2 for validation and JSON serialisation.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ── KB Article (canonical unit stored in ChromaDB + audit log) ──────────────

class KBArticle(BaseModel):
    """One knowledge-base article derived from a support ticket."""
    article_id: str                        # maps to ticket_id
    title: str                             # auto-generated from category + product
    category: str
    subcategory: str
    product: str
    severity: str
    summary: str
    resolution: str
    created_date: date
    resolved_date: date
    resolution_time_hrs: float
    sla_met: bool
    is_repetitive: bool
    tags: list[str] = Field(default_factory=list)
    status: Literal["active", "stale", "archived", "merged"] = "active"
    quality_score: Optional[float] = None  # 0-1, filled by audit agent


# ── Audit findings ───────────────────────────────────────────────────────────

class AuditFinding(BaseModel):
    """A single finding produced by the Audit Agent."""
    finding_id: str
    finding_type: Literal["duplicate", "stale", "contradiction", "low_quality", "gap"]
    affected_articles: list[str]           # article_ids involved
    severity: Literal["low", "medium", "high", "critical"]
    description: str
    evidence: str                          # why the agent thinks this is a problem
    suggested_action: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Remediation proposals ────────────────────────────────────────────────────

class RemediationProposal(BaseModel):
    """A change proposed by the Remediation Agent, pending human review."""
    proposal_id: str
    finding_id: str
    action: Literal["rewrite", "merge", "retire", "create", "update_tags"]
    target_articles: list[str]
    proposed_content: str                  # new or merged text
    rationale: str
    verifier_score: Optional[float] = None # filled by Verifier agent (0-1)
    verifier_notes: Optional[str] = None
    status: Literal["pending", "approved", "rejected", "applied"] = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    reviewer_notes: Optional[str] = None


# ── Audit log entry (every agent action recorded here) ───────────────────────

class AuditLogEntry(BaseModel):
    """Immutable record of every agent action — the demo's proof of autonomy."""
    entry_id: str
    agent: str                             # which agent took the action
    action: str                            # what it did
    inputs: dict                           # what it received
    outputs: dict                          # what it produced
    sources: list[str]                     # article/ticket IDs referenced
    outcome: Literal["success", "failure", "partial"]
    notes: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── Q&A response ─────────────────────────────────────────────────────────────

class QAResponse(BaseModel):
    """Structured answer from the Q&A Agent."""
    question: str
    answer: str
    citations: list[str]                   # article_ids cited
    confidence: float = Field(ge=0.0, le=1.0)
    sources_used: int
    caveat: Optional[str] = None           # e.g. "KB may be incomplete on this topic"


# ── Orchestrator task ─────────────────────────────────────────────────────────

class OrchestratorTask(BaseModel):
    """A step in the orchestrator's plan."""
    step: int
    agent: str
    description: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    result_summary: Optional[str] = None
