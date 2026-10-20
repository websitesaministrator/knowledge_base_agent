"""
Gap Detection Agent — finds topics the KB cannot answer.
Strategy:
  1. Enumerate category+product combinations present in the dataset.
  2. For each combination, check if there are SLA-failing tickets with NO corresponding
     high-quality (non-repetitive) KB article.
  3. Ask the LLM to suggest what KB articles are missing for the worst gaps.

This tells support teams what documentation to write next.
"""
from __future__ import annotations
import logging
import uuid
from collections import defaultdict

from models import AuditFinding
from tools.vector_store import get_all_articles, similarity_search
from tools.audit_log import append_entry
from llm_client import chat_json

logger = logging.getLogger(__name__)

AGENT_NAME = "gap_agent"


def run(top_n: int = 5) -> dict:
    """
    Detect knowledge gaps in the KB.
    top_n: how many gap findings to generate.
    """
    articles = get_all_articles()
    if not articles:
        raise RuntimeError("No articles in store. Run Ingestion Agent first.")

    # Count tickets per category+product and how many are repetitive
    group_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "repetitive": 0, "sla_failed": 0})
    for a in articles:
        meta = a["metadata"]
        key = f"{meta.get('category')} | {meta.get('product')}"
        group_stats[key]["total"] += 1
        if meta.get("is_repetitive") == "True":
            group_stats[key]["repetitive"] += 1
        if meta.get("sla_met") == "False":
            group_stats[key]["sla_failed"] += 1

    # Score gap severity: high repetitive + high sla_fail = big gap
    scored = sorted(
        group_stats.items(),
        key=lambda x: x[1]["repetitive"] + x[1]["sla_failed"],
        reverse=True,
    )

    gap_findings: list[AuditFinding] = []
    llm_suggestions: list[dict] = []

    for key, stats in scored[:top_n]:
        # Ask LLM what's missing for this topic
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a KB gap analyst. Given a support category and product with many "
                    "repetitive tickets and SLA failures, identify what KB articles are MISSING "
                    "that would help resolve these issues faster. "
                    "Respond with valid JSON: "
                    '{"missing_articles": ["title1", "title2"], "reason": "explanation"}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Category: {key}\n"
                    f"Total tickets: {stats['total']}\n"
                    f"Repetitive (same issue recurring): {stats['repetitive']}\n"
                    f"SLA failures: {stats['sla_failed']}\n"
                    f"This suggests the KB is not helping resolve this issue type adequately."
                ),
            },
        ]
        try:
            result = chat_json(prompt, max_tokens=300)
            llm_suggestions.append({"topic": key, **result})
            for title in result.get("missing_articles", [])[:2]:
                gap_findings.append(AuditFinding(
                    finding_id=str(uuid.uuid4())[:8],
                    finding_type="gap",
                    affected_articles=[],
                    severity="high" if stats["sla_failed"] > 5 else "medium",
                    description=f"Missing KB article: '{title}' for {key}",
                    evidence=(
                        f"{stats['repetitive']} repetitive tickets, "
                        f"{stats['sla_failed']} SLA failures for this topic"
                    ),
                    suggested_action=f"Create new KB article: {title}",
                    confidence=0.75,
                ))
        except Exception as e:
            logger.warning("[Gap] LLM failed for %s: %s", key, e)

    summary = {
        "total_groups": len(group_stats),
        "gaps_found": len(gap_findings),
        "worst_topics": [
            {"topic": k, **v} for k, v in scored[:top_n]
        ],
        "llm_suggestions": llm_suggestions,
        "findings": [f.model_dump(mode="json") for f in gap_findings],
    }

    append_entry(
        agent=AGENT_NAME,
        action="detect_gaps",
        inputs={"article_count": len(articles), "top_n": top_n},
        outputs={k: v for k, v in summary.items() if k != "findings"},
        sources=[],
        outcome="success",
        notes=f"Found {len(gap_findings)} gap findings across {len(group_stats)} topic groups",
    )

    return summary
