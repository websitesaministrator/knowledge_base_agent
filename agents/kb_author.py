"""
agents/kb_author.py - KB Author Agent.
Synthesizes evidence-grounded KB article drafts for support ticket clusters.
Grounds all facts in retrieved ticket evidence; marks AI troubleshooting steps explicitly.
"""
from __future__ import annotations
import logging
from typing import Any
from agents.base import run_agent
from tools.tickets import get_cluster_tickets, cluster_stats, fix_usage_map, data_quality_report
from tools.article_store import KBArticleDraft, save_article, cluster_to_slug

logger = logging.getLogger(__name__)

AGENT_NAME = "KBAuthor"

INSTRUCTION = (
    "You write ONE KB article draft for ONE ticket cluster using ONLY evidence you retrieve with tools. "
    "MANDATORY REQUIREMENTS FOR EVERY ARTICLE:\n"
    "1. 'what_we_see': State plainly that ticket summaries contain only templated category wording "
    "('User reported <category> while using X') with NO symptom detail. Do NOT invent symptoms or use words like "
    "'unexpected', 'unanticipated', or 'intermittent' that are not in the raw data. "
    "Report products from the product column with exact ticket counts, and explicitly note the "
    "summary-vs-product-column mismatch (found in 242/300 rows across the overall dataset). Cite sample ticket IDs.\n"
    "2. 'resolutions_on_record': List each fix ID with ticket_count and other_categories_count (from fix_usage_map). "
    "State plainly that fix content is not in the data (tickets only state 'Resolved by applying standard fix #N from KB'), "
    "and state how many other categories cite each fix ID.\n"
    "3. 'suggested_steps': Maximum 5 generic, cautious troubleshooting steps. Every step MUST have label "
    "'AI-suggested, unverified'. There must be NO causal claims (do NOT use words like 'to avoid', 'because', 'in order to', or 'so that').\n"
    "4. 'needs_sme_input': List specific procedural questions a human SME must confirm.\n"
    "5. If Verifier feedback is provided, fix exactly the flagged issues and re-output the full article.\n"
    "Never invent ticket IDs, counts, fix content, or product behaviour."
)


def draft_article(
    cluster_id: str,
    version: int = 1,
    window: str = "baseline",
    verifier_feedback: str | None = None,
    max_steps: int = 8,
) -> dict:
    """
    Generate a factual KB article draft for a specific cluster using only retrieved evidence.
    Saves the validated draft via article_store.
    Returns: dict {run_id, agent, content, data, status, steps, article}
    """
    slug = cluster_to_slug(cluster_id)
    expected_article_id = f"KB-{slug}-v{version}"

    feedback_prompt = ""
    if verifier_feedback:
        feedback_prompt = f"\nCRITICAL VERIFIER FEEDBACK TO FIX:\n{verifier_feedback}\nAddress these issues precisely."

    user_input = (
        f"Write a comprehensive KB article draft for cluster '{cluster_id}' in the '{window}' window.\n"
        f"Use tools to inspect cluster tickets, cluster stats, fix usage map, and data quality report.\n"
        f"Follow all 4 mandatory formatting and factual constraints strictly.\n"
        f"The article_id MUST be '{expected_article_id}', cluster_id must be '{cluster_id}', and version must be {version}.\n"
        f"Required JSON structure:\n"
        f"{{\n"
        f'  "article_id": "{expected_article_id}",\n'
        f'  "cluster_id": "{cluster_id}",\n'
        f'  "title": "<procedural title>",\n'
        f'  "version": {version},\n'
        f'  "status": "draft",\n'
        f'  "what_we_see": "<facts only: templated summaries note, product column counts, 242/300 mismatch note, citing tickets>",\n'
        f'  "resolutions_on_record": [{{"fix_id": <int>, "ticket_count": <int>, "other_categories_count": <int>, "notes": "<string>"}}],\n'
        f'  "suggested_steps": [{{"text": "<guidance without causal claims>", "label": "AI-suggested, unverified"}}],\n'
        f'  "needs_sme_input": ["<what SME must confirm>"],\n'
        f'  "evidence": {{"ticket_ids": ["..."], "ticket_count": <int>, "repetitive_rate": <float>}}\n'
        f"}}\n"
        f"Output strictly valid JSON conforming to this schema.{feedback_prompt}"
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
        output_schema=KBArticleDraft,
        max_steps=max_steps,
    )

    saved_article = None
    if result.get("data"):
        try:
            # Ensure required identifiers are consistent
            result["data"]["article_id"] = expected_article_id
            result["data"]["cluster_id"] = cluster_id
            result["data"]["version"] = version
            saved_article = save_article(result["data"])
            result["article"] = saved_article
        except Exception as e:
            logger.error("Failed to save article to store: %s", e)

    return result
