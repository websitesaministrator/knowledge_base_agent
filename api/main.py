"""
FastAPI application — the demo's API layer.
All agent entry points are exposed as REST endpoints.
The UI calls these. Judges can also hit them directly.

Background tasks are used for the long-running lifecycle sweep so the HTTP
response returns immediately with a run_id, and the UI polls /status/{run_id}.
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
from pydantic import BaseModel

from config import settings

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory run state (use Redis/Firestore in production)
_runs: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("KB Lifecycle Agent API starting up")
    yield
    logger.info("KB Lifecycle Agent API shutting down")


app = FastAPI(
    title="KB Lifecycle Agent",
    description="AI-powered multi-agent system for knowledge base lifecycle management (PS-03)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the static UI
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")


# ── Request/Response models ───────────────────────────────────────────────────

class OrchestrateRequest(BaseModel):
    skip_llm_audit: bool = False
    max_proposals: int = 6
    gap_top_n: int = 5


class QARequest(BaseModel):
    question: str
    n_results: int = 5


class ReviewRequest(BaseModel):
    notes: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse("ui/index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "kb-lifecycle-agent"}


@app.post("/api/orchestrate")
async def orchestrate(req: OrchestrateRequest, background_tasks: BackgroundTasks):
    """
    Start a full KB lifecycle sweep in the background.
    Returns immediately with a run_id for status polling.
    """
    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {"status": "running", "result": None}

    def _run():
        from agents.orchestrator import run_lifecycle_sweep
        try:
            result = run_lifecycle_sweep(
                skip_llm_audit=req.skip_llm_audit,
                max_remediation_proposals=req.max_proposals,
                gap_top_n=req.gap_top_n,
            )
            _runs[run_id] = {"status": "done", "result": result}
        except Exception as e:
            logger.error("Lifecycle sweep failed: %s", e)
            _runs[run_id] = {"status": "error", "result": {"error": str(e)}}

    background_tasks.add_task(_run)
    return {"run_id": run_id, "message": "Lifecycle sweep started"}


@app.get("/api/status/{run_id}")
async def run_status(run_id: str):
    """Poll the status of a background lifecycle sweep."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run ID not found")
    return run


@app.post("/api/ingest")
async def ingest():
    """Trigger ingestion only (useful for demo setup)."""
    from agents.ingestion_agent import run
    result = run()
    return result


@app.post("/api/audit")
async def audit(skip_llm: bool = False):
    """Run the audit agent only."""
    from agents.audit_agent import run
    result = run(skip_llm=skip_llm)
    return result


@app.post("/api/qa")
async def qa(req: QARequest):
    """Answer a question using the KB."""
    from agents.qa_agent import answer
    result = answer(req.question, n_results=req.n_results)
    return result.model_dump()


@app.get("/api/articles")
async def list_articles(limit: int = 50):
    """List KB articles from the vector store."""
    from tools.vector_store import get_all_articles
    articles = get_all_articles(limit=limit)
    return {"total": len(articles), "articles": articles}


@app.get("/api/audit-log")
async def audit_log():
    """Return the full audit log — proof of agent autonomy."""
    from tools.audit_log import get_all_entries
    entries = get_all_entries()
    return {"total": len(entries), "entries": entries}


@app.get("/api/review-queue")
async def review_queue():
    """Return all proposals in the review queue."""
    from tools.review_queue import get_all
    proposals = get_all()
    return {"total": len(proposals), "proposals": proposals}


@app.post("/api/review/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, req: ReviewRequest):
    """Human approves a remediation proposal."""
    from tools.review_queue import approve
    p = approve(proposal_id, reviewer_notes=req.notes)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    # Log the human action
    from tools.audit_log import append_entry
    append_entry(
        agent="human_reviewer",
        action="approve_proposal",
        inputs={"proposal_id": proposal_id},
        outputs={"status": "approved"},
        sources=[proposal_id],
        outcome="success",
        notes=req.notes or "Human approved via UI",
    )
    return p


@app.post("/api/review/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, req: ReviewRequest):
    """Human rejects a remediation proposal."""
    from tools.review_queue import reject
    p = reject(proposal_id, reviewer_notes=req.notes)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    from tools.audit_log import append_entry
    append_entry(
        agent="human_reviewer",
        action="reject_proposal",
        inputs={"proposal_id": proposal_id},
        outputs={"status": "rejected"},
        sources=[proposal_id],
        outcome="success",
        notes=req.notes or "Human rejected via UI",
    )
    return p


@app.post("/api/reset")
async def reset():
    """Reset the vector store and audit log for a fresh demo run."""
    from tools.vector_store import reset_collection
    from tools.audit_log import clear_log
    reset_collection()
    clear_log()
    _runs.clear()
    return {"message": "System reset complete"}
