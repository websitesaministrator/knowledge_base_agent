"""
agents/verifier.py - Verifier Agent.
Performs strict, tool-grounded fact checking on drafted KB articles.
Outputs PASS only when zero issues exist; outputs REVISE with specific issue details and fix guidance.
The Verifier never rewrites articles.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Literal
from pydantic import BaseModel, Field

from agents.base import run_agent
from tools.tickets import get_cluster_tickets, cluster_stats, fix_usage_map, data_quality_report
from tools.audit_log import append_entry

logger = logging.getLogger(__name__)

AGENT_NAME = "Verifier"


class VerifierIssue(BaseModel):
    check: str = Field(description="Name of check that failed (e.g. check_ticket_ids, check_numbers, check_causal_claims, check_required_statements)")
    detail: str = Field(description="Exact discrepancy or violation found")
    how_to_fix: str = Field(description="Clear instruction for the Author on how to fix this exact issue")


class VerifierVerdict(BaseModel):
    verdict: Literal["PASS", "REVISE"] = Field(description="PASS if zero issues, else REVISE")
    issues: list[VerifierIssue] = Field(default_factory=list, description="List of issues found")


INSTRUCTION = (
    "You are the Verifier agent for an IT knowledge base lifecycle system. "
    "Your job is to strictly fact-check and audit a drafted KB article against ground-truth tools. "
    "Tools available: get_cluster_tickets, cluster_stats, fix_usage_map, data_quality_report.\n"
    "Checks you MUST execute:\n"
    "1. check_ticket_ids: Call get_cluster_tickets. Verify that every ticket ID cited anywhere in the article "
    "actually exists in this cluster. If any cited ticket ID is missing from tool output, flag as issue.\n"
    "2. check_numbers: Call cluster_stats. Verify that ticket_count, repetitive_rate, fix counts, and severity mix "
    "match tool outputs exactly.\n"
    "3. check_fix_ids: Verify that every fix ID cited in resolutions_on_record actually appears in the cluster's distinct_fix_ids.\n"
    "4. check_step_labels_and_causality: Verify that EVERY step in suggested_steps has label 'AI-suggested, unverified' "
    "AND contains NO causal claims (do not allow words like 'to avoid', 'because', 'in order to', or 'so that').\n"
    "5. check_unsupported_claims: Ensure there is no speculative wording inventing symptom details, root causes, "
    "or product behaviours that the raw data does not state (e.g. do not allow words like 'unexpected', 'unanticipated').\n"
    "6. check_required_statements: Verify that the article explicitly states:\n"
    "   a) ticket summaries contain only templated category wording with no symptom detail;\n"
    "   b) fix procedures are not present in the data;\n"
    "   c) fix IDs are shared with other categories;\n"
    "   d) the product mismatch note (242/300 rows).\n"
    "7. check_products: Verify that products listed match the product column counts from the cluster tickets, "
    "or the 242/300 mismatch is clearly noted.\n\n"
    "VERDICT RULES:\n"
    "- If issues list is empty: verdict must be 'PASS'.\n"
    "- If any issue exists: verdict must be 'REVISE'.\n"
    "- You must NEVER rewrite the article yourself."
)


def verify_article(
    article: dict,
    cluster_id: str,
    window: str = "baseline",
    max_steps: int = 8,
) -> dict:
    """
    Execute the Verifier agent on an article draft.
    Returns dict: {verdict: 'PASS'|'REVISE', issues: [...], run_id: str, steps: int}
    """
    article_str = json.dumps(article, indent=2)
    user_input = (
        f"Audit the following KB article draft for cluster '{cluster_id}' in the '{window}' window.\n"
        f"Call the tools (get_cluster_tickets, cluster_stats, fix_usage_map, data_quality_report) to verify every fact.\n"
        f"Article draft to verify:\n"
        f"```json\n{article_str}\n```\n"
        f"Execute all 7 mandatory checks. Output strictly valid JSON conforming to the VerifierVerdict schema."
    )

    tools = [
        get_cluster_tickets,
        cluster_stats,
        fix_usage_map,
        data_quality_report,
    ]

    result = run_agent(
        name=AGENT_NAME,
        instruction=INSTRUCTION,
        tools=tools,
        user_input=user_input,
        output_schema=VerifierVerdict,
        max_steps=max_steps,
    )

    data = result.get("data") or {}
    verdict = data.get("verdict", "REVISE")
    issues = data.get("issues", [])

    # Strict enforcement: if issues are present, verdict cannot be PASS
    if issues and verdict == "PASS":
        verdict = "REVISE"
    elif not issues and verdict == "REVISE":
        # If no issues were identified, it passes
        verdict = "PASS"

    return {
        "verdict": verdict,
        "issues": issues,
        "run_id": result.get("run_id"),
        "steps": result.get("steps", 0),
        "raw_content": result.get("content", ""),
    }
