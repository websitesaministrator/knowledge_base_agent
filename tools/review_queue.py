"""
Review queue tool — stores RemediationProposals pending human approval.
In-memory + JSON file for the demo (Firestore in production).
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import RemediationProposal

logger = logging.getLogger(__name__)
_QUEUE_FILE = Path("review_queue.json")
_queue: dict[str, dict] = {}  # proposal_id -> serialised proposal


def _load() -> None:
    global _queue
    if _QUEUE_FILE.exists():
        try:
            with open(_QUEUE_FILE, encoding="utf-8") as f:
                _queue = json.load(f)
        except Exception:
            _queue = {}


def _save() -> None:
    with open(_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(_queue, f, indent=2, default=str)


def enqueue(proposal: RemediationProposal) -> str:
    """Add a proposal to the review queue. Returns the proposal_id."""
    if not _queue:
        _load()
    _queue[proposal.proposal_id] = proposal.model_dump(mode="json")
    _save()
    logger.info("Enqueued proposal %s (%s)", proposal.proposal_id, proposal.action)
    return proposal.proposal_id


def get_pending() -> list[dict]:
    """Return all proposals with status='pending'."""
    if not _queue:
        _load()
    return [p for p in _queue.values() if p.get("status") == "pending"]


def get_all() -> list[dict]:
    if not _queue:
        _load()
    return list(_queue.values())


def approve(proposal_id: str, reviewer_notes: str = "") -> Optional[dict]:
    """Mark a proposal as approved."""
    if not _queue:
        _load()
    p = _queue.get(proposal_id)
    if not p:
        return None
    p["status"] = "approved"
    p["reviewed_at"] = datetime.utcnow().isoformat()
    p["reviewer_notes"] = reviewer_notes
    _save()
    logger.info("Proposal %s APPROVED", proposal_id)
    return p


def reject(proposal_id: str, reviewer_notes: str = "") -> Optional[dict]:
    """Mark a proposal as rejected."""
    if not _queue:
        _load()
    p = _queue.get(proposal_id)
    if not p:
        return None
    p["status"] = "rejected"
    p["reviewed_at"] = datetime.utcnow().isoformat()
    p["reviewer_notes"] = reviewer_notes
    _save()
    logger.info("Proposal %s REJECTED", proposal_id)
    return p


def get_proposal(proposal_id: str) -> Optional[dict]:
    if not _queue:
        _load()
    return _queue.get(proposal_id)
