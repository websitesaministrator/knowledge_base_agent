"""
Orchestrator / Planner Agent — the brain of the system.

Responsibilities:
  1. Accept a high-level goal ("run a full KB lifecycle sweep")
  2. Break it into a plan (list of OrchestratorTasks)
  3. Execute each step by calling the right agent
  4. Handle failures: retry once, then mark step as failed and continue
  5. Return a full run summary

Why sequential plan not parallel: the demo needs a clear, explainable step order.
Parallelism would complicate the audit log story for judges.
"""
from __future__ import annotations
import logging
import time
from typing import Callable

from models import OrchestratorTask
from tools.audit_log import append_entry
import agents.ingestion_agent as ingestion_agent
import agents.audit_agent as audit_agent
import agents.remediation_agent as remediation_agent
import agents.verifier_agent as verifier_agent
import agents.gap_agent as gap_agent

logger = logging.getLogger(__name__)
AGENT_NAME = "orchestrator"

# Max retries per step — guards against runaway loops
MAX_STEP_RETRIES = 1


def _run_step_with_retry(fn: Callable, *args, **kwargs):
    """Run a callable with one retry on failure."""
    for attempt in range(MAX_STEP_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt < MAX_STEP_RETRIES:
                logger.warning("[Orchestrator] Step failed (attempt %d): %s — retrying", attempt + 1, e)
                time.sleep(2)
            else:
                raise


def run_lifecycle_sweep(
    data_path: str | None = None,
    skip_llm_audit: bool = False,
    max_remediation_proposals: int = 6,
    gap_top_n: int = 5,
) -> dict:
    """
    Execute the full KB lifecycle sweep end-to-end.
    Called by the /api/orchestrate endpoint.

    Returns a dict with each stage's results for the UI to display.
    """
    plan = [
        OrchestratorTask(step=1, agent="ingestion_agent", description="Load and embed KB articles from CSV"),
        OrchestratorTask(step=2, agent="audit_agent", description="Detect duplicates, stale, low-quality, contradictions"),
        OrchestratorTask(step=3, agent="remediation_agent", description="Draft fixes for audit findings"),
        OrchestratorTask(step=4, agent="verifier_agent", description="Self-correct: score and reject weak proposals"),
        OrchestratorTask(step=5, agent="gap_agent", description="Identify missing KB articles"),
    ]

    results: dict[str, dict] = {}
    audit_findings: list[dict] = []
    proposals: list[dict] = []

    logger.info("[Orchestrator] Starting lifecycle sweep with %d steps", len(plan))

    append_entry(
        agent=AGENT_NAME,
        action="plan_lifecycle_sweep",
        inputs={"steps": len(plan), "skip_llm_audit": skip_llm_audit},
        outputs={"plan": [t.model_dump() for t in plan]},
        sources=[],
        outcome="success",
        notes="Orchestrator built a plan and is dispatching agents",
    )

    for task in plan:
        task.status = "running"
        logger.info("[Orchestrator] Step %d: %s", task.step, task.description)

        try:
            if task.agent == "ingestion_agent":
                result = _run_step_with_retry(ingestion_agent.run, data_path=data_path)

            elif task.agent == "audit_agent":
                result = _run_step_with_retry(audit_agent.run, skip_llm=skip_llm_audit)
                audit_findings = result.get("findings", [])

            elif task.agent == "remediation_agent":
                result = _run_step_with_retry(
                    remediation_agent.run,
                    findings=audit_findings,
                    max_proposals=max_remediation_proposals,
                )
                # Fetch the created proposals for verifier
                import tools.review_queue as rq
                proposals = rq.get_pending()

            elif task.agent == "verifier_agent":
                result = _run_step_with_retry(verifier_agent.run, proposal_dicts=proposals)

            elif task.agent == "gap_agent":
                result = _run_step_with_retry(gap_agent.run, top_n=gap_top_n)

            else:
                raise ValueError(f"Unknown agent: {task.agent}")

            task.status = "done"
            task.result_summary = str(result)[:200]
            results[task.agent] = result

        except Exception as e:
            task.status = "failed"
            task.result_summary = str(e)
            results[task.agent] = {"error": str(e)}
            logger.error("[Orchestrator] Step %d FAILED: %s", task.step, e)
            # Continue to next step — partial results are better than nothing
            append_entry(
                agent=AGENT_NAME,
                action=f"step_{task.step}_failed",
                inputs={"agent": task.agent},
                outputs={"error": str(e)},
                sources=[],
                outcome="failure",
                notes=f"Step {task.step} ({task.agent}) failed; continuing sweep",
            )

    final_summary = {
        "plan": [t.model_dump() for t in plan],
        "results": results,
        "total_steps": len(plan),
        "completed": sum(1 for t in plan if t.status == "done"),
        "failed": sum(1 for t in plan if t.status == "failed"),
    }

    append_entry(
        agent=AGENT_NAME,
        action="lifecycle_sweep_complete",
        inputs={},
        outputs={
            "completed": final_summary["completed"],
            "failed": final_summary["failed"],
        },
        sources=[],
        outcome="success" if final_summary["failed"] == 0 else "partial",
        notes=f"Sweep complete: {final_summary['completed']}/{final_summary['total_steps']} steps succeeded",
    )

    return final_summary
