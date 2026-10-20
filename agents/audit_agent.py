"""
Audit Agent — analyses the KB for:
  1. Duplicates   — articles with high semantic similarity (cosine distance < threshold)
  2. Stale content — articles where is_repetitive=True and sla_met=False repeatedly
  3. Low quality  — very short resolution notes
  4. Contradictions — same category+product with very different resolutions (LLM judge)

Why LLM for contradictions: rule-based similarity catches near-duplicates,
but spotting semantic contradictions ("restart the service" vs "do NOT restart")
requires language understanding.

Each finding is returned and optionally written to the review queue via Remediation Agent.
"""
from __future__ import annotations
import logging
import uuid
from itertools import combinations

from models import AuditFinding
from tools.vector_store import get_all_articles, similarity_search
from tools.audit_log import append_entry
from llm_client import chat_json

logger = logging.getLogger(__name__)

AGENT_NAME = "audit_agent"

# Thresholds (tuned for cosine distance; lower = more similar)
DUPLICATE_DISTANCE_THRESHOLD = 0.12   # very similar
STALE_RESOLUTION_TIME_THRESHOLD = 96  # hours — P1 tickets taking >4 days are flagged
LOW_QUALITY_MIN_WORDS = 6             # resolution notes shorter than this are flagged


def _detect_duplicates(articles: list[dict]) -> list[AuditFinding]:
    """
    For each article, search for near-duplicates via ChromaDB.
    We query each article's own text and check if another article is very close.
    Capped at checking 50 pairs to avoid O(n²) LLM calls.
    """
    findings: list[AuditFinding] = []
    seen_pairs: set[frozenset] = set()

    # Sample — audit all if < 100 articles, else sample 100 worst candidates
    # (is_repetitive=True articles are prime duplicate suspects)
    candidates = [a for a in articles if a["metadata"].get("is_repetitive") == "True"]
    if not candidates:
        candidates = articles[:50]

    for article in candidates[:80]:  # guard: max 80 queries
        results = similarity_search(article["document"], n_results=4)
        for r in results:
            if r["id"] == article["id"]:
                continue  # skip self
            pair = frozenset([article["id"], r["id"]])
            if pair in seen_pairs:
                continue
            if r["distance"] < DUPLICATE_DISTANCE_THRESHOLD:
                seen_pairs.add(pair)
                findings.append(AuditFinding(
                    finding_id=str(uuid.uuid4())[:8],
                    finding_type="duplicate",
                    affected_articles=[article["id"], r["id"]],
                    severity="medium",
                    description=(
                        f"Articles {article['id']} and {r['id']} are near-identical "
                        f"(cosine dist={r['distance']:.3f}). Both cover "
                        f"{article['metadata'].get('category')} for "
                        f"{article['metadata'].get('product')}."
                    ),
                    evidence=f"Cosine distance: {r['distance']:.4f} (threshold: {DUPLICATE_DISTANCE_THRESHOLD})",
                    suggested_action="Merge into a single canonical article",
                    confidence=min(1.0, (DUPLICATE_DISTANCE_THRESHOLD - r["distance"] + 0.1) * 5),
                ))

    logger.info("[Audit] Found %d duplicate pairs", len(findings))
    return findings


def _detect_stale(articles: list[dict]) -> list[AuditFinding]:
    """
    Flag articles where SLA was missed AND resolution took very long.
    These tickets signal the KB resolution guidance was inadequate.
    """
    findings: list[AuditFinding] = []
    for a in articles:
        meta = a["metadata"]
        if (
            meta.get("sla_met") == "False"
            and float(meta.get("resolution_time_hrs", 0)) > STALE_RESOLUTION_TIME_THRESHOLD
        ):
            findings.append(AuditFinding(
                finding_id=str(uuid.uuid4())[:8],
                finding_type="stale",
                affected_articles=[a["id"]],
                severity="high",
                description=(
                    f"Article {a['id']} ({meta.get('category')} / {meta.get('product')}) "
                    f"had resolution time {meta.get('resolution_time_hrs')}h with SLA missed. "
                    f"The KB guidance for this issue is likely outdated or insufficient."
                ),
                evidence=f"resolution_time_hrs={meta.get('resolution_time_hrs')}, sla_met=False",
                suggested_action="Review and rewrite the resolution procedure",
                confidence=0.85,
            ))
    logger.info("[Audit] Found %d stale articles", len(findings))
    return findings


def _detect_low_quality(articles: list[dict]) -> list[AuditFinding]:
    """Flag articles with very short resolution notes — likely placeholder content."""
    findings: list[AuditFinding] = []
    for a in articles:
        resolution_text = a["metadata"].get("resolution", "") or a["document"]
        word_count = len(resolution_text.split())
        if word_count < LOW_QUALITY_MIN_WORDS:
            findings.append(AuditFinding(
                finding_id=str(uuid.uuid4())[:8],
                finding_type="low_quality",
                affected_articles=[a["id"]],
                severity="low",
                description=f"Article {a['id']} has very short resolution notes ({word_count} words).",
                evidence=f"Word count: {word_count} < threshold {LOW_QUALITY_MIN_WORDS}",
                suggested_action="Expand with detailed step-by-step resolution procedure",
                confidence=0.9,
            ))
    logger.info("[Audit] Found %d low-quality articles", len(findings))
    return findings


def _detect_contradictions_llm(articles: list[dict], max_groups: int = 5) -> list[AuditFinding]:
    """
    Use LLM to detect contradictory resolutions within the same category+product.
    We group articles by (category, product) and feed pairs to the LLM.
    Limited to max_groups to control API cost.
    """
    findings: list[AuditFinding] = []

    # Group by category+product
    groups: dict[str, list[dict]] = {}
    for a in articles:
        key = f"{a['metadata'].get('category')}|{a['metadata'].get('product')}"
        groups.setdefault(key, []).append(a)

    checked = 0
    for key, group in groups.items():
        if checked >= max_groups:
            break
        if len(group) < 2:
            continue

        # Take 2 representative articles from this group
        a1, a2 = group[0], group[1]
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a KB quality auditor. Given two knowledge base articles "
                    "on the same topic, decide if their resolution advice CONTRADICTS each other. "
                    "Respond only with valid JSON: "
                    '{"contradicts": true/false, "reason": "short explanation", "confidence": 0.0-1.0}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Article 1 ({a1['id']}):\n{a1['document']}\n\n"
                    f"Article 2 ({a2['id']}):\n{a2['document']}"
                ),
            },
        ]
        try:
            result = chat_json(prompt, max_tokens=200)
            if result.get("contradicts") and result.get("confidence", 0) > 0.6:
                findings.append(AuditFinding(
                    finding_id=str(uuid.uuid4())[:8],
                    finding_type="contradiction",
                    affected_articles=[a1["id"], a2["id"]],
                    severity="high",
                    description=(
                        f"Contradictory resolutions found for {key.replace('|', ' / ')}: "
                        f"{result.get('reason', 'see evidence')}"
                    ),
                    evidence=f"LLM confidence: {result.get('confidence', 0):.2f}",
                    suggested_action="Review both articles and consolidate into one accurate resolution",
                    confidence=float(result.get("confidence", 0.7)),
                ))
        except Exception as e:
            logger.warning("[Audit] Contradiction check failed for %s: %s", key, e)

        checked += 1

    logger.info("[Audit] Found %d contradictions", len(findings))
    return findings


def run(skip_llm: bool = False) -> dict:
    """
    Main entry point called by the Orchestrator.
    Returns all findings grouped by type.
    skip_llm=True skips the contradiction LLM check (faster, useful for testing).
    """
    articles = get_all_articles()
    if not articles:
        raise RuntimeError("No articles in store. Run Ingestion Agent first.")

    logger.info("[Audit] Auditing %d articles...", len(articles))

    all_findings: list[AuditFinding] = []
    all_findings += _detect_duplicates(articles)
    all_findings += _detect_stale(articles)
    all_findings += _detect_low_quality(articles)
    if not skip_llm:
        all_findings += _detect_contradictions_llm(articles)

    summary = {
        "total_articles": len(articles),
        "total_findings": len(all_findings),
        "duplicates": sum(1 for f in all_findings if f.finding_type == "duplicate"),
        "stale": sum(1 for f in all_findings if f.finding_type == "stale"),
        "low_quality": sum(1 for f in all_findings if f.finding_type == "low_quality"),
        "contradictions": sum(1 for f in all_findings if f.finding_type == "contradiction"),
        "findings": [f.model_dump(mode="json") for f in all_findings],
    }

    append_entry(
        agent=AGENT_NAME,
        action="audit_kb",
        inputs={"article_count": len(articles), "skip_llm": skip_llm},
        outputs={k: v for k, v in summary.items() if k != "findings"},
        sources=[f.affected_articles[0] for f in all_findings[:10]],
        outcome="success",
        notes=f"Found {len(all_findings)} issues across {len(articles)} articles",
    )

    return summary
