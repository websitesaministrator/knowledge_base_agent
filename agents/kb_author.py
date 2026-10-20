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
    "You write ONE KB article draft for ONE ticket cluster using ONLY evidence you retrieve with tools.\n"
    "MANDATORY REQUIREMENTS FOR EVERY ARTICLE:\n"
    "1. 'title': Use the real cluster name and variant (e.g. 'Billing issues (variant 3): recurring tickets'). NEVER use 'Category 3' or generic placeholders.\n"
    "2. 'what_we_see': Narrative facts only. State plainly that ticket summaries contain only templated category wording "
    "('User reported <category> while using X') with NO symptom detail. Do NOT invent symptoms or use words like "
    "'unexpected', 'unanticipated', or 'intermittent'. Note the 242/300 summary-vs-product mismatch. "
    "CRITICAL: Do NOT state any numbers in what_we_see that are absent from the structured fields (products, severity_mix, resolutions_on_record, evidence).\n"
    "3. 'products': List exact product counts matching the product column: [{'name': '<Product>', 'count': <int>}].\n"
    "4. 'severity_mix': List exact severity counts matching tool output: [{'severity': 'P1'|'P2'|'P3'|'P4', 'count': <int>}].\n"
    "5. 'resolutions_on_record': List each fix ID with its ticket_count and other_categories_count. "
    "IMPORTANT: other_categories_count MUST be (total categories using this fix) - 1. State plainly that fix procedures are not in data.\n"
    "6. 'suggested_triage_steps': Maximum 5 generic steps about GATHERING and CHECKING information "
    "(what to collect from the user, which prior tickets and recorded fixes to look up, when to escalate to an SME). "
    "NO step may change a system (do NOT use reset, update, restart, reconfigure, install, modify, apply, delete, reboot) "
    "and NONE may carry a causal claim (do NOT use 'to avoid', 'because', 'so that', 'in order to'). "
    "Every step MUST be labelled 'AI-suggested, unverified'.\n"
    "7. 'needs_sme_input': Specific procedural questions a human SME must confirm.\n"
    "8. If Verifier feedback is provided, fix exactly the flagged issues and re-output the full article.\n"
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

    cat, subcat = cluster_id.split("|", 1) if "|" in cluster_id else (cluster_id, "")
    variant_suffix = subcat.replace(cat, "").strip(" -")
    suggested_title = f"{cat} issues ({variant_suffix}): recurring tickets" if variant_suffix else f"{cat}: recurring tickets"

    feedback_prompt = ""
    if verifier_feedback:
        feedback_prompt = f"\nCRITICAL VERIFIER FEEDBACK TO FIX:\n{verifier_feedback}\nAddress these issues precisely."

    user_input = (
        f"Write a comprehensive KB article draft for cluster '{cluster_id}' in the '{window}' window.\n"
        f"Use tools to inspect cluster tickets, cluster stats, fix usage map, and data quality report.\n"
        f"Follow all mandatory formatting and factual constraints strictly.\n"
        f"The article_id MUST be '{expected_article_id}', cluster_id must be '{cluster_id}', and version must be {version}.\n"
        f"Use title: '{suggested_title}'.\n"
        f"Required JSON structure:\n"
        f"{{\n"
        f'  "article_id": "{expected_article_id}",\n'
        f'  "cluster_id": "{cluster_id}",\n'
        f'  "title": "{suggested_title}",\n'
        f'  "version": {version},\n'
        f'  "status": "draft",\n'
        f'  "what_we_see": "<facts only narrative: templated wording note, product mismatch note, citing sample ticket IDs>",\n'
        f'  "products": [{{"name": "<exact product>", "count": <int>}}],\n'
        f'  "severity_mix": [{{"severity": "P1"|"P2"|"P3"|"P4", "count": <int>}}],\n'
        f'  "resolutions_on_record": [{{"fix_id": <int>, "ticket_count": <int>, "other_categories_count": <int>, "notes": "<string>"}}],\n'
        f'  "suggested_triage_steps": [{{"text": "<gathering/checking info step>", "label": "AI-suggested, unverified"}}],\n'
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
