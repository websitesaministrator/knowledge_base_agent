"""
Verifier Agent — the self-correction loop.
Given a RemediationProposal, it:
  1. Retrieves the most relevant articles from ChromaDB to use as ground truth.
  2. Asks the LLM to score the proposal against those articles.
  3. Returns a score (0-1) and notes. Low scores trigger rejection.

This is the "self-correction" behaviour required by the hackathon judges.
The Orchestrator runs Verifier before enqueuing proposals for human review.
"""
from __future__ import annotations
import logging
import uuid

from models import RemediationProposal
from tools.vector_store import similarity_search
from tools.review_queue import get_proposal, get_all
from tools.audit_log import append_entry
from llm_client import chat_json

logger = logging.getLogger(__name__)

AGENT_NAME = "verifier_agent"

ACCEPTANCE_THRESHOLD = 0.6  # proposals below this score are auto-rejected


def verify_proposal(proposal: RemediationProposal) -> tuple[float, str]:
    """
    Verify a single proposal.
    Returns (score, notes) where score is 0-1.
    """
    # Retrieve ground-truth articles related to the proposal's topic
    context_hits = similarity_search(proposal.proposed_content, n_results=4)
    context = "\n\n".join(h["document"] for h in context_hits)

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a KB quality verifier. Given a proposed KB article update "
                "and relevant existing articles as context, score the proposal on: "
                "1) Factual consistency with context, "
                "2) Clarity and actionability, "
                "3) No hallucinations or contradictions with context. "
                "Respond only with valid JSON: "
                '{"score": 0.0-1.0, "notes": "brief explanation", "issues": ["list of problems if any"]}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"--- Proposed update ---\n{proposal.proposed_content}\n\n"
                f"--- Relevant KB context ---\n{context}"
            ),
        },
    ]

    result = chat_json(prompt, max_tokens=300)
    score = float(result.get("score", 0.5))
    notes = result.get("notes", "") + " Issues: " + ", ".join(result.get("issues", []))
    return score, notes


def run(proposal_dicts: list[dict]) -> dict:
    """
    Verify all pending proposals.
    Updates each proposal's verifier_score and verifier_notes in the queue.
    Auto-rejects proposals below ACCEPTANCE_THRESHOLD.
    """
    import tools.review_queue as rq  # late import to avoid circular

    accepted: list[str] = []
    rejected: list[str] = []
    errors: list[str] = []

    for raw in proposal_dicts:
        try:
            proposal = RemediationProposal(**raw)
            score, notes = verify_proposal(proposal)

            # Update in queue
            p = rq.get_proposal(proposal.proposal_id)
            if p:
                p["verifier_score"] = score
                p["verifier_notes"] = notes
                # Write back
                rq._queue[proposal.proposal_id] = p
                rq._save()

            if score >= ACCEPTANCE_THRESHOLD:
                accepted.append(proposal.proposal_id)
                logger.info("[Verifier] %s ACCEPTED (score=%.2f)", proposal.proposal_id, score)
            else:
                # Auto-reject weak proposals — self-correction in action
                rq.reject(proposal.proposal_id, reviewer_notes=f"Auto-rejected by Verifier: {notes}")
                rejected.append(proposal.proposal_id)
                logger.info("[Verifier] %s REJECTED (score=%.2f): %s", proposal.proposal_id, score, notes)

        except Exception as e:
            logger.error("[Verifier] Error on %s: %s", raw.get("proposal_id"), e)
            errors.append(str(e))

    summary = {
        "verified": len(accepted) + len(rejected),
        "accepted": len(accepted),
        "auto_rejected": len(rejected),
        "errors": len(errors),
        "accepted_ids": accepted,
        "rejected_ids": rejected,
    }

    append_entry(
        agent=AGENT_NAME,
        action="verify_proposals",
        inputs={"proposals_received": len(proposal_dicts), "threshold": ACCEPTANCE_THRESHOLD},
        outputs=summary,
        sources=accepted + rejected,
        outcome="success" if not errors else "partial",
        notes=f"Self-correction: {len(rejected)} proposals auto-rejected below score {ACCEPTANCE_THRESHOLD}",
    )

    return summary
