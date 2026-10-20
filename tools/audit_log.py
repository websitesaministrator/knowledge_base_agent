"""
Audit log tool — append-only in-memory store (JSON-serialisable).
In production this would write to Firestore; for the demo it persists to a local JSON file.
Every agent action MUST call append_entry() so judges can see the proof of autonomy.
"""
from __future__ import annotations
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from models import AuditLogEntry

logger = logging.getLogger(__name__)

_LOG_FILE = Path("audit_log.json")
_log: list[dict] = []


def _load() -> None:
    """Load existing log from disk on first use."""
    global _log
    if _LOG_FILE.exists():
        try:
            with open(_LOG_FILE, encoding="utf-8") as f:
                _log = json.load(f)
        except Exception:
            _log = []


def _save() -> None:
    with open(_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(_log, f, indent=2, default=str)


def append_entry(
    agent: str,
    action: str,
    inputs: dict,
    outputs: dict,
    sources: list[str],
    outcome: str = "success",
    notes: str = "",
) -> AuditLogEntry:
    """
    Record one agent action.  Called by every agent after each significant step.
    Returns the created entry for callers that want to chain it.
    """
    if not _log:
        _load()

    entry = AuditLogEntry(
        entry_id=str(uuid.uuid4())[:8],
        agent=agent,
        action=action,
        inputs=inputs,
        outputs=outputs,
        sources=sources,
        outcome=outcome,
        notes=notes,
    )
    _log.append(entry.model_dump(mode="json"))
    _save()
    logger.info("[AUDIT] %s | %s | %s", agent, action, outcome)
    return entry


def get_all_entries() -> list[dict]:
    if not _log:
        _load()
    return list(_log)


def clear_log() -> None:
    """Used in tests and fresh demo runs only."""
    global _log
    _log = []
    if _LOG_FILE.exists():
        _LOG_FILE.unlink()
