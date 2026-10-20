"""
agents/kb_author.py - KB Author Agent.
Synthesizes evidence-grounded KB article drafts for support ticket clusters.
Grounds all facts in retrieved ticket evidence; marks AI troubleshooting steps explicitly.
"""
from __future__ import annotations
import logging
from typing import Any
from agents.base import run_agent
from tools.tickets import get_cluster_tickets, cluster_stats, fix_usage_map
from tools.article_store import KBArticleDraft, save_article, cluster_to_slug

logger = logging.getLogger(__name__)

AGENT_NAME = "KBAuthor"

INSTRUCTION = (
    "You write ONE KB article draft for ONE ticket cluster using ONLY evidence you retrieve with tools. "
    "The data has no real fix procedures: resolution notes only reference 'standard fix #N'. So: "
    "'what_we_see' = facts only (symptom wording from ticket summaries, volume, repetitive rate, products, severity mix), citing ticket IDs. "
    "'resolutions_on_record' = fix IDs with counts; state plainly that fix content is not in the data, and that fix IDs are shared across other categories where true. "
    "'suggested_steps' = general, cautious steps, maximum 5, every one labelled 'AI-suggested, unverified', no product-specific claims you cannot support. "
    "'needs_sme_input' = what a human must confirm. "
    "If the Verifier returned issues, fix exactly those and re-output the full article. "
    "Never invent ticket IDs, counts, fix content or product behaviour."
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
        f"Use tools to inspect cluster tickets, cluster stats, and fix usage map.\n"
        f"The article_id MUST be '{expected_article_id}', cluster_id must be '{cluster_id}', and version must be {version}.\n"
        f"Required JSON structure:\n"
        f"{{\n"
        f'  "article_id": "{expected_article_id}",\n'
        f'  "cluster_id": "{cluster_id}",\n'
        f'  "title": "<procedural title>",\n'
        f'  "version": {version},\n'
        f'  "status": "draft",\n'
        f'  "what_we_see": "<factual summary with ticket IDs>",\n'
        f'  "resolutions_on_record": [{{"fix_id": <int>, "ticket_count": <int>}}],\n'
        f'  "suggested_steps": [{{"text": "<guidance>", "label": "AI-suggested, unverified"}}],\n'
        f'  "needs_sme_input": ["<what SME must confirm>"],\n'
        f'  "evidence": {{"ticket_ids": ["..."], "ticket_count": <int>, "repetitive_rate": <float>}}\n'
        f"}}\n"
        f"Output strictly valid JSON conforming to this schema.{feedback_prompt}"
    )

    tools = [
        get_cluster_tickets,
        cluster_stats,
        fix_usage_map,
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
