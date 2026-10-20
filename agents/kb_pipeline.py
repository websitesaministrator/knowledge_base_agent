"""
agents/kb_pipeline.py - Knowledge Base Synthesis Pipeline.
Coordinates Author -> Verifier self-correction loop.
On REVISE, sends structured critique back to Author for targeted revision (max 2 revisions).
On PASS, marks status 'pending_review'. If still failing after 2 rounds, flags 'verifier_unresolved'.
Logs all round verdicts and issues to the audit log.
"""
from __future__ import annotations
import logging
from typing import Any

from agents.kb_author import draft_article
from agents.verifier import verify_article
from tools.article_store import save_article, get_article
from tools.audit_log import append_entry

logger = logging.getLogger(__name__)


def run_synthesis_pipeline(
    cluster_id: str,
    version: int = 1,
    window: str = "baseline",
    max_revisions: int = 2,
) -> dict:
    """
    Run complete synthesis pipeline: Author -> Verifier loop.
    Allows up to max_revisions corrections.
    """
    rounds_history: list[dict[str, Any]] = []

    # Round 1: Initial Draft
    logger.info("Pipeline: Starting Round 1 draft for '%s'", cluster_id)
    author_res = draft_article(cluster_id=cluster_id, version=version, window=window)
    current_draft = author_res.get("data") or {}

    if not current_draft:
        logger.error("Author failed to produce initial draft for '%s'", cluster_id)
        return {
            "cluster_id": cluster_id,
            "status": "failed",
            "error": "Author failed to produce draft",
            "rounds": [],
        }

    # Verify Round 1
    verifier_res = verify_article(current_draft, cluster_id=cluster_id, window=window)
    verdict = verifier_res.get("verdict", "REVISE")
    issues = verifier_res.get("issues", [])

    append_entry(
        agent="Verifier",
        action="verify_round:1",
        inputs={
            "article_id": current_draft.get("article_id"),
            "cluster_id": cluster_id,
            "round": 1,
        },
        outputs={
            "verdict": verdict,
            "issue_count": len(issues),
            "issues": [i if isinstance(i, dict) else i.model_dump() for i in issues],
        },
        sources=current_draft.get("evidence", {}).get("ticket_ids", []),
        outcome="success" if verdict == "PASS" else "partial",
        notes=f"Round 1 verification: verdict={verdict}, issues={len(issues)}",
    )

    rounds_history.append({
        "round": 1,
        "verdict": verdict,
        "issues": [i if isinstance(i, dict) else i.model_dump() for i in issues],
        "draft_title": current_draft.get("title"),
    })

    # Revision Loop (up to max_revisions)
    current_round = 1
    while verdict == "REVISE" and current_round <= max_revisions:
        current_round += 1
        logger.info("Pipeline: Starting Revision Round %d for '%s'", current_round, cluster_id)

        feedback_lines = []
        for iss in issues:
            iss_dict = iss if isinstance(iss, dict) else iss.model_dump()
            feedback_lines.append(
                f"- Check '{iss_dict.get('check')}': {iss_dict.get('detail')}. "
                f"Action required: {iss_dict.get('how_to_fix')}"
            )
        feedback_str = "\n".join(feedback_lines)

        # Author revises targeting the exact issues
        revised_author_res = draft_article(
            cluster_id=cluster_id,
            version=version,
            window=window,
            verifier_feedback=feedback_str,
        )
        revised_draft = revised_author_res.get("data") or {}

        if revised_draft:
            current_draft = revised_draft

        # Verifier checks revised draft
        verifier_res = verify_article(current_draft, cluster_id=cluster_id, window=window)
        verdict = verifier_res.get("verdict", "REVISE")
        issues = verifier_res.get("issues", [])

        append_entry(
            agent="Verifier",
            action=f"verify_round:{current_round}",
            inputs={
                "article_id": current_draft.get("article_id"),
                "cluster_id": cluster_id,
                "round": current_round,
            },
            outputs={
                "verdict": verdict,
                "issue_count": len(issues),
                "issues": [i if isinstance(i, dict) else i.model_dump() for i in issues],
            },
            sources=current_draft.get("evidence", {}).get("ticket_ids", []),
            outcome="success" if verdict == "PASS" else "partial",
            notes=f"Round {current_round} verification: verdict={verdict}, issues={len(issues)}",
        )

        rounds_history.append({
            "round": current_round,
            "verdict": verdict,
            "issues": [i if isinstance(i, dict) else i.model_dump() for i in issues],
            "draft_title": current_draft.get("title"),
        })

    # Finalize article state
    current_draft["status"] = "pending_review"
    if verdict == "PASS":
        current_draft["verifier_unresolved"] = False
        current_draft["open_issues"] = []
    else:
        current_draft["verifier_unresolved"] = True
        current_draft["open_issues"] = [i if isinstance(i, dict) else i.model_dump() for i in issues]

    final_article = save_article(current_draft)

    return {
        "cluster_id": cluster_id,
        "article_id": final_article.get("article_id"),
        "final_verdict": verdict,
        "total_rounds": len(rounds_history),
        "status": final_article.get("status"),
        "verifier_unresolved": final_article.get("verifier_unresolved", False),
        "rounds": rounds_history,
        "article": final_article,
    }
