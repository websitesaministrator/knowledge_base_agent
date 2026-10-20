"""
agents/qa_agent.py - Q&A / Knowledge Assistant Agent.
Answers user support questions using the synthesized and maintained KB articles in article_store.
Cites verified article IDs (e.g. KB-billing-billing-variant-3-v1), standard fixes, and suggested triage steps.
"""
from __future__ import annotations
import logging
from typing import Any

from models import QAResponse
from tools.article_store import list_articles
from tools.audit_log import append_entry
from llm_client import chat_json

logger = logging.getLogger(__name__)

AGENT_NAME = "QAAgent"


def answer(question: str, n_results: int = 3) -> QAResponse:
    """
    Answer a question using the maintained KB article store.
    Returns a QAResponse with answer, citations, and confidence score.
    """
    articles = list_articles()

    if not articles:
        return QAResponse(
            question=question,
            answer="The knowledge base is currently empty. Run the Lifecycle Pipeline to synthesize and verify articles from ticket evidence.",
            citations=[],
            confidence=0.0,
            sources_used=0,
            caveat="KB empty. Synthesize articles first.",
        )

    # Rank/match articles based on simple query relevance against cluster, title, and what_we_see
    q_lower = question.lower()
    scored_articles = []
    for art in articles:
        text_corpus = (
            f"{art.get('title', '')} {art.get('cluster_id', '')} "
            f"{art.get('what_we_see', '')} "
            f"{' '.join(p.get('name', '') for p in art.get('products', []))} "
            f"{' '.join(str(r.get('fix_id', '')) for r in art.get('resolutions_on_record', []))}"
        ).lower()

        # Score based on keyword overlap
        score = sum(1 for word in q_lower.split() if len(word) > 2 and word in text_corpus)
        scored_articles.append((score, art))

    scored_articles.sort(key=lambda x: x[0], reverse=True)
    top_articles = [art for _, art in scored_articles[:n_results]]

    # Build context from the matched articles
    context_blocks = []
    for art in top_articles:
        prods_str = ", ".join(f"{p.get('name')}: {p.get('count')}" for p in art.get("products", []))
        fixes_str = ", ".join(f"Fix #{r.get('fix_id')} (used {r.get('ticket_count')}x, in {r.get('other_categories_count')} other categories)" for r in art.get("resolutions_on_record", []))
        triage_str = "\n".join(f"  - [{s.get('label', 'AI-suggested')}]: {s.get('text')}" for s in art.get("suggested_triage_steps", []))

        block = (
            f"Article ID: [{art.get('article_id')}]\n"
            f"Cluster: {art.get('cluster_id')}\n"
            f"Title: {art.get('title')}\n"
            f"Status: {art.get('status')}\n"
            f"What We See: {art.get('what_we_see')}\n"
            f"Products: {prods_str}\n"
            f"Resolutions on Record: {fixes_str}\n"
            f"Suggested Triage Steps:\n{triage_str}\n"
        )
        context_blocks.append(block)

    context = "\n---\n".join(context_blocks)

    prompt = [
        {
            "role": "system",
            "content": (
                "You are an expert IT Support Knowledge Base Assistant.\n"
                "Answer the user's question using ONLY the facts and triage steps in the provided KB articles.\n"
                "Rules:\n"
                "1. Always cite the relevant article ID (e.g. [KB-billing-billing-variant-3-v1]).\n"
                "2. Mention the specific products involved and standard fix IDs recorded in the evidence.\n"
                "3. Provide non-destructive troubleshooting steps (gathering info, checking accounts, SME escalation) clearly labeled 'AI-suggested, unverified'.\n"
                "4. If the KB does not contain enough info, acknowledge the gap honestly.\n"
                "Output strictly valid JSON with format:\n"
                '{"answer": "...", "citations": ["KB-..."], "confidence": 0.0-1.0, "caveat": "optional caveat or null"}'
            ),
        },
        {
            "role": "user",
            "content": f"User Question: {question}\n\nMaintained Knowledge Base:\n{context}",
        },
    ]

    try:
        result = chat_json(prompt, max_tokens=600)
    except Exception as e:
        logger.error("QA LLM call failed: %s", e)
        result = {
            "answer": f"Unable to generate answer at this time: {e}",
            "citations": [art.get("article_id") for art in top_articles if art.get("article_id")],
            "confidence": 0.3,
            "caveat": "Error querying model",
        }

    response = QAResponse(
        question=question,
        answer=result.get("answer", "No answer generated."),
        citations=result.get("citations", [art.get("article_id") for art in top_articles if art.get("article_id")]),
        confidence=float(result.get("confidence", 0.85)),
        sources_used=len(top_articles),
        caveat=result.get("caveat"),
    )

    append_entry(
        agent=AGENT_NAME,
        action="answer_question",
        inputs={"question": question, "n_results": n_results},
        outputs={
            "answer_preview": response.answer[:200],
            "confidence": response.confidence,
            "citations": response.citations,
        },
        sources=response.citations,
        outcome="success",
        notes=f"Answered query with confidence {response.confidence:.2f} using {len(top_articles)} verified articles",
    )

    return response
