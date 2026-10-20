"""
Remediation Agent — takes audit findings and drafts concrete fixes:
  - duplicate → merge into one canonical article
  - stale     → rewrite the resolution procedure
  - low_quality → expand the resolution
  - contradiction → consolidate into a single correct resolution

Each proposal goes to the review queue for human approval before being applied.
"""
from __future__ import annotations
import logging
import uuid

from models import AuditFinding, RemediationProposal
from tools.review_queue import enqueue
from tools.audit_log import append_entry
from tools.vector_store import get_article
from llm_client import chat_completion

logger = logging.getLogger(__name__)

AGENT_NAME = "remediation_agent"


def _draft_merge(finding: AuditFinding) -> RemediationProposal:
    """Ask the LLM to merge two near-duplicate articles."""
    articles_text = ""
    for aid in finding.affected_articles[:2]:
        a = get_article(aid)
        if a:
            articles_text += f"\n--- {aid} ---\n{a['document']}\n"

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a technical writer. Merge the following two duplicate KB articles "
                "into a single, clear, actionable article. Keep all unique information. "
                "Format: start with 'Title:', then 'Summary:', then 'Resolution:' with numbered steps."
            ),
        },
        {"role": "user", "content": articles_text},
    ]
    merged = chat_completion(prompt, max_tokens=600)
    return RemediationProposal(
        proposal_id=str(uuid.uuid4())[:8],
        finding_id=finding.finding_id,
        action="merge",
        target_articles=finding.affected_articles,
        proposed_content=merged,
        rationale=f"Articles are near-duplicates (finding {finding.finding_id}). Merged to reduce KB noise.",
    )


def _draft_rewrite(finding: AuditFinding) -> RemediationProposal:
    """Ask the LLM to rewrite a stale or low-quality resolution."""
    aid = finding.affected_articles[0]
    a = get_article(aid)
    article_text = a["document"] if a else f"Article {aid} not found"

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a technical writer improving a support knowledge base. "
                "Rewrite the following article to have a clear, step-by-step resolution procedure. "
                "Make it specific and actionable. "
                "Format: 'Title:', 'Summary:', 'Resolution:' with numbered steps."
            ),
        },
        {"role": "user", "content": article_text},
    ]
    rewritten = chat_completion(prompt, max_tokens=500)
    action = "rewrite" if finding.finding_type == "stale" else "update_tags"
    return RemediationProposal(
        proposal_id=str(uuid.uuid4())[:8],
        finding_id=finding.finding_id,
        action="rewrite",
        target_articles=[aid],
        proposed_content=rewritten,
        rationale=f"Article flagged as {finding.finding_type}: {finding.description}",
    )


def _draft_consolidation(finding: AuditFinding) -> RemediationProposal:
    """Ask the LLM to resolve a contradiction between two articles."""
    articles_text = ""
    for aid in finding.affected_articles[:2]:
        a = get_article(aid)
        if a:
            articles_text += f"\n--- {aid} ---\n{a['document']}\n"

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a senior support engineer. Two KB articles contradict each other. "
                "Write one definitive, accurate resolution that resolves the contradiction. "
                "If you cannot determine the correct answer, state that and recommend escalation. "
                "Format: 'Title:', 'Summary:', 'Resolution:' with numbered steps."
            ),
        },
        {"role": "user", "content": articles_text},
    ]
    consolidated = chat_completion(prompt, max_tokens=600)
    return RemediationProposal(
        proposal_id=str(uuid.uuid4())[:8],
        finding_id=finding.finding_id,
        action="merge",
        target_articles=finding.affected_articles,
        proposed_content=consolidated,
        rationale=f"Contradiction detected: {finding.description}",
    )


def run(findings: list[dict], max_proposals: int = 10) -> dict:
    """
    Process audit findings and create proposals in the review queue.
    max_proposals guards against generating too many LLM calls.
    """
    proposals: list[RemediationProposal] = []
    errors: list[str] = []
    processed = 0

    for raw in findings:
        if processed >= max_proposals:
            logger.info("[Remediation] Reached max_proposals=%d, stopping", max_proposals)
            break

        try:
            finding = AuditFinding(**raw)
            if finding.finding_type == "duplicate":
                proposal = _draft_merge(finding)
            elif finding.finding_type in ("stale", "low_quality"):
                proposal = _draft_rewrite(finding)
            elif finding.finding_type == "contradiction":
                proposal = _draft_consolidation(finding)
            else:
                # Gap findings handled by Gap Agent
                continue

            enqueue(proposal)
            proposals.append(proposal)
            processed += 1

        except Exception as e:
            logger.error("[Remediation] Failed on finding %s: %s", raw.get("finding_id"), e)
            errors.append(str(e))

    summary = {
        "proposals_created": len(proposals),
        "errors": len(errors),
        "proposal_ids": [p.proposal_id for p in proposals],
    }

    append_entry(
        agent=AGENT_NAME,
        action="draft_remediation",
        inputs={"findings_received": len(findings), "max_proposals": max_proposals},
        outputs=summary,
        sources=[p.target_articles[0] for p in proposals],
        outcome="success" if not errors else "partial",
        notes=f"Created {len(proposals)} proposals for review",
    )

    return summary
