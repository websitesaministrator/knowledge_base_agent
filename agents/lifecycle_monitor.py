"""
agents/lifecycle_monitor.py - Lifecycle Monitor Agent.
Evaluates published KB articles against incoming replay tickets (>= 2026-05-01).
Decides KEEP, UPDATE, or RETIRE based on statistical drift signals from compute_cluster_signals.
Proposes status changes via lifecycle_proposal without modifying actual article status directly.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Literal
from pydantic import BaseModel, Field

from config import settings
from agents.base import run_agent
from tools.tickets import compute_cluster_signals, get_cluster_tickets
from tools.article_store import get_article, list_articles, save_article
from tools.audit_log import append_entry

logger = logging.getLogger(__name__)

AGENT_NAME = "LifecycleMonitor"


class LifecycleProposal(BaseModel):
    article_id: str = Field(description="ID of the KB article evaluated")
    decision: Literal["KEEP", "UPDATE", "RETIRE"] = Field(description="Lifecycle decision: KEEP, UPDATE, or RETIRE")
    reasons: list[str] = Field(description="Justifications for the decision, stating exact numbers from tools")
    signals: dict[str, Any] = Field(description="Cluster drift signals returned by compute_cluster_signals")
    proposed_status: Literal["published", "flagged_stale", "retired"] = Field(
        description="Proposed status: published (KEEP), flagged_stale (UPDATE), or retired (RETIRE)"
    )
    update_request: str | None = Field(
        default=None,
        description="Details of what should be updated if decision is UPDATE, else null"
    )


INSTRUCTION = (
    "You review published KB articles against NEW tickets (the replay window). "
    "Call compute_cluster_signals for the article's cluster, then decide KEEP, UPDATE or RETIRE, "
    "justifying with numbers from the tool. "
    f"Starting defaults (live in config.py): "
    f"UPDATE if a fix ID the article does not cite covers {int(settings.lifecycle_new_fix_share_threshold * 100)}% or more of replay tickets in the cluster, "
    f"or replay repetitive_rate is {settings.lifecycle_rep_rate_threshold} or higher while the baseline was lower, "
    f"or average resolution time rose {int(settings.lifecycle_avg_res_increase_threshold * 100)}% or more; "
    f"RETIRE if the cluster has {settings.lifecycle_retire_ticket_threshold} or fewer replay tickets; "
    "otherwise KEEP. "
    "You propose; you never change status. Use only tool results."
)


def evaluate_article_lifecycle(
    article_id: str,
    max_steps: int = 8,
) -> dict[str, Any]:
    """
    Run Lifecycle Monitor on a published article.
    Inspects article, queries compute_cluster_signals for the cluster,
    decides KEEP/UPDATE/RETIRE, and saves proposal to article.lifecycle_proposal.
    Returns: dict {run_id, decision, proposal, article, steps}
    """
    article = get_article(article_id)
    if not article:
        logger.error("LifecycleMonitor: Article %s not found in store", article_id)
        return {"error": f"Article '{article_id}' not found"}

    cluster_id = article.get("cluster_id", "")
    current_status = article.get("status", "published")

    user_input = (
        f"Review published KB article '{article_id}' for cluster '{cluster_id}' against new replay tickets.\n"
        f"1. Call get_article to inspect the article's cited fixes and content.\n"
        f"2. Call compute_cluster_signals for cluster '{cluster_id}'.\n"
        f"3. Evaluate thresholds from config:\n"
        f"   - RETIRE if replay ticket count <= {settings.lifecycle_retire_ticket_threshold};\n"
        f"   - UPDATE if a fix ID not cited in the article covers >= {settings.lifecycle_new_fix_share_threshold:.0%} of replay tickets, "
        f"or replay repetitive_rate is >= {settings.lifecycle_rep_rate_threshold} while baseline was lower, "
        f"or average resolution time rose >= {settings.lifecycle_avg_res_increase_threshold:.0%};\n"
        f"   - otherwise KEEP.\n"
        f"4. Propose status: 'published' for KEEP, 'flagged_stale' for UPDATE, 'retired' for RETIRE.\n"
        f"Output strictly valid JSON conforming to the LifecycleProposal schema with exact numbers in reasons."
    )

    tools = [
        compute_cluster_signals,
        get_cluster_tickets,
        get_article,
        list_articles,
    ]

    result = run_agent(
        name=AGENT_NAME,
        instruction=INSTRUCTION,
        tools=tools,
        user_input=user_input,
        output_schema=LifecycleProposal,
        max_steps=max_steps,
    )

    proposal_data = result.get("data")
    if proposal_data:
        try:
            # Ensure article_id matches
            proposal_data["article_id"] = article_id
            proposal = LifecycleProposal.model_validate(proposal_data)

            # Save proposal to article WITHOUT changing article status
            article["lifecycle_proposal"] = proposal.model_dump()
            updated_article = save_article(article)

            append_entry(
                agent=AGENT_NAME,
                action="lifecycle_decision",
                inputs={"article_id": article_id, "cluster_id": cluster_id, "current_status": current_status},
                outputs={
                    "decision": proposal.decision,
                    "proposed_status": proposal.proposed_status,
                    "reasons": proposal.reasons,
                    "update_request": proposal.update_request,
                },
                sources=article.get("evidence", {}).get("ticket_ids", []),
                outcome="success",
                notes=f"Lifecycle proposal for {article_id}: {proposal.decision} ({proposal.proposed_status})",
            )

            return {
                "article_id": article_id,
                "cluster_id": cluster_id,
                "current_status": current_status,
                "decision": proposal.decision,
                "proposed_status": proposal.proposed_status,
                "proposal": proposal.model_dump(),
                "article": updated_article,
                "run_id": result.get("run_id"),
                "steps": result.get("steps", 0),
            }
        except Exception as e:
            logger.error("Failed to validate or save lifecycle proposal: %s", e)

    return result
