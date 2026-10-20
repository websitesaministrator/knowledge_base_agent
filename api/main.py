"""
FastAPI application — KB Lifecycle Agent API Layer.
Exposes multi-agent orchestration, cluster analytics, article store,
lifecycle evaluation, Q&A assistant, and human-in-the-loop review.
"""
from __future__ import annotations
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

_runs: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("KB Lifecycle Agent API starting up")
    yield
    logger.info("KB Lifecycle Agent API shutting down")


app = FastAPI(
    title="KB Lifecycle Agent",
    description="Autonomous Multi-Agent System for Support Ticket KB Lifecycle Management",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")


# ── Request/Response models ───────────────────────────────────────────────────

class OrchestrateRequest(BaseModel):
    cluster_id: str = "Billing|Billing - variant 3"


class QARequest(BaseModel):
    question: str
    n_results: int = 3


class ReviewRequest(BaseModel):
    notes: str = ""


# ── Core Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse("ui/index.html")


@app.get("/app.js")
async def get_app_js():
    return FileResponse("ui/app.js")


@app.get("/style.css")
async def get_style_css():
    return FileResponse("ui/style.css")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "kb-lifecycle-agent", "version": "2.0.0"}


@app.post("/api/orchestrate")
async def orchestrate(req: OrchestrateRequest, background_tasks: BackgroundTasks):
    """
    Start the autonomous end-to-end multi-agent lifecycle sweep in the background.
    Returns immediately with run_id for live progress polling.
    """
    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {"status": "running", "result": None}

    def _run():
        from agents.orchestrator import run_lifecycle_sweep
        try:
            result = run_lifecycle_sweep(cluster_id=req.cluster_id)
            _runs[run_id] = {"status": "done", "result": result}
        except Exception as e:
            logger.error("Lifecycle sweep failed: %s", e)
            _runs[run_id] = {"status": "error", "result": {"error": str(e)}}

    background_tasks.add_task(_run)
    return {"run_id": run_id, "message": "Multi-agent lifecycle sweep started", "cluster_id": req.cluster_id}


@app.get("/api/status/{run_id}")
async def run_status(run_id: str):
    """Poll status and step outputs of a background lifecycle sweep."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run ID not found")
    return run


@app.get("/api/clusters")
async def get_clusters(window: str = "baseline"):
    """Get all 24 prioritized ticket clusters with multi-factor scores."""
    from tools.tickets import score_clusters
    return score_clusters(window=window)


@app.get("/api/articles")
async def list_kb_articles(status: str | None = None):
    """List synthesized KB articles from the store."""
    from tools.article_store import list_articles
    articles = list_articles(status=status)
    return {"total": len(articles), "articles": articles}


@app.get("/api/articles/{article_id}")
async def get_kb_article(article_id: str):
    """Retrieve full structured KB article by article_id."""
    from tools.article_store import get_article
    art = get_article(article_id)
    if not art:
        raise HTTPException(status_code=404, detail="Article not found")
    return art


@app.get("/api/signals")
async def get_drift_signals():
    """Get replay drift signals and lifecycle decision spread across all 24 clusters."""
    from tools.tickets import cluster_stats, compute_cluster_signals
    from scripts.run_lifecycle_demo import evaluate_cluster_drift
    from collections import Counter

    cs = cluster_stats(window="all")["clusters"]
    all_clusters = sorted(list(cs.keys()))
    results = [evaluate_cluster_drift(cid) for cid in all_clusters]
    counts = Counter(r["decision"] for r in results)

    return {
        "total": len(results),
        "summary": dict(counts),
        "clusters": results,
    }


@app.post("/api/qa")
async def qa(req: QARequest):
    """Answer support questions using the maintained KB."""
    from agents.qa_agent import answer
    result = answer(req.question, n_results=req.n_results)
    return result.model_dump()


@app.get("/api/audit-log")
async def audit_log():
    """Return chronological audit log — proof of agent autonomy & tool verification."""
    from tools.audit_log import get_all_entries
    entries = get_all_entries()
    return {"total": len(entries), "entries": entries}


@app.get("/api/review-queue")
async def review_queue():
    """Return articles that have pending lifecycle proposals or unverified flags."""
    from tools.article_store import list_articles
    articles = list_articles()
    pending = []
    for a in articles:
        if a.get("lifecycle_proposal") or a.get("verifier_unresolved") or a.get("status") == "pending_review":
            pending.append({
                "article_id": a.get("article_id"),
                "cluster_id": a.get("cluster_id"),
                "title": a.get("title"),
                "status": a.get("status"),
                "proposal": a.get("lifecycle_proposal"),
                "verifier_unresolved": a.get("verifier_unresolved", False),
            })
    return {"total": len(pending), "proposals": pending}


@app.post("/api/review/{article_id}/approve")
async def approve_proposal(article_id: str, req: ReviewRequest):
    """Human SME approves a proposed lifecycle status change."""
    from tools.article_store import get_article, save_article
    from tools.audit_log import append_entry

    art = get_article(article_id)
    if not art:
        raise HTTPException(status_code=404, detail="Article not found")

    proposal = art.get("lifecycle_proposal")
    new_status = proposal.get("proposed_status") if proposal else "published"
    old_status = art.get("status")

    art["status"] = new_status
    art["lifecycle_proposal"] = None  # Resolved
    save_article(art)

    append_entry(
        agent="human_reviewer",
        action="approve_lifecycle_proposal",
        inputs={"article_id": article_id, "previous_status": old_status, "new_status": new_status},
        outputs={"status": "approved", "final_status": new_status},
        sources=[article_id],
        outcome="success",
        notes=req.notes or f"Human SME approved transition to {new_status}",
    )
    return {"message": f"Proposal approved. Article status transitioned to {new_status}.", "article": art}


@app.post("/api/review/{article_id}/reject")
async def reject_proposal(article_id: str, req: ReviewRequest):
    """Human SME rejects a proposed lifecycle status change."""
    from tools.article_store import get_article, save_article
    from tools.audit_log import append_entry

    art = get_article(article_id)
    if not art:
        raise HTTPException(status_code=404, detail="Article not found")

    art["lifecycle_proposal"] = None  # Dismiss proposal
    save_article(art)

    append_entry(
        agent="human_reviewer",
        action="reject_lifecycle_proposal",
        inputs={"article_id": article_id},
        outputs={"status": "rejected"},
        sources=[article_id],
        outcome="success",
        notes=req.notes or "Human SME rejected lifecycle proposal; maintaining current status",
    )
    return {"message": "Proposal rejected. Article status unchanged.", "article": art}


@app.post("/api/reset")
async def reset():
    """Reset audit log and temporary run caches."""
    from tools.audit_log import clear_log
    clear_log()
    _runs.clear()
    return {"message": "Audit log and run cache cleared successfully"}
