"""
Q&A Agent — answers user questions using the KB (RAG pattern).
Retrieves the most relevant articles from ChromaDB, then asks the LLM
to synthesise a cited, confidence-scored answer.

This is the "serve" stage: the maintained KB is now queryable.
"""
from __future__ import annotations
import logging

from models import QAResponse
from tools.vector_store import similarity_search
from tools.audit_log import append_entry
from llm_client import chat_json

logger = logging.getLogger(__name__)

AGENT_NAME = "qa_agent"


def answer(question: str, n_results: int = 5) -> QAResponse:
    """
    Answer a question using the KB.
    Returns a QAResponse with answer, citations, and confidence.
    """
    # Step 1: retrieve relevant articles
    hits = similarity_search(question, n_results=n_results)

    if not hits:
        return QAResponse(
            question=question,
            answer="The knowledge base is empty. Please run ingestion first.",
            citations=[],
            confidence=0.0,
            sources_used=0,
            caveat="KB not populated.",
        )

    # Step 2: build context from retrieved articles
    context = ""
    for h in hits:
        context += f"\n[{h['id']}] {h['document']}\n"

    # Step 3: ask LLM to synthesise answer with citations
    prompt = [
        {
            "role": "system",
            "content": (
                "You are a helpful support assistant. Answer the user's question "
                "using ONLY the provided KB articles. "
                "Cite article IDs like [TKT-XXXXX] in your answer. "
                "Be specific and actionable. If the KB doesn't cover the question, say so. "
                "Respond with valid JSON: "
                '{"answer": "...", "citations": ["TKT-...", ...], "confidence": 0.0-1.0, '
                '"caveat": "optional caveat or null"}'
            ),
        },
        {
            "role": "user",
            "content": f"Question: {question}\n\nKB Articles:\n{context}",
        },
    ]

    result = chat_json(prompt, max_tokens=600)

    response = QAResponse(
        question=question,
        answer=result.get("answer", "No answer generated."),
        citations=result.get("citations", [h["id"] for h in hits[:3]]),
        confidence=float(result.get("confidence", 0.5)),
        sources_used=len(hits),
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
        notes=f"Answered with confidence {response.confidence:.2f} using {len(hits)} articles",
    )

    return response
