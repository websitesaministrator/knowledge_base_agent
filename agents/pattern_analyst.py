"""
agents/pattern_analyst.py - Pattern Analyst Agent.
Evaluates ticket clusters from statistical evidence and selects candidate clusters for KB synthesis.
Built on run_agent with deterministic pandas tools.
"""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

from agents.base import run_agent
from tools.tickets import (
    data_quality_report,
    cluster_stats,
    fix_usage_map,
    get_cluster_tickets,
)

AGENT_NAME = "PatternAnalyst"

INSTRUCTION = (
    "You are the Pattern Analyst for a support-ticket knowledge base. "
    "Goal: decide which ticket clusters deserve a KB article, and why. Use tools; never guess numbers. "
    "Steps: 1) call data_quality_report once and note the issues. "
    "2) call cluster_stats for the baseline window. "
    "3) call fix_usage_map. "
    "4) Score each cluster higher for more tickets, higher repetitive_rate, more distinct fix IDs, "
    "no dominant fix, and a higher share of P1/P2 tickets. "
    "5) Pick the top N clusters (N given by the caller, default 6) and call get_cluster_tickets for each to confirm the evidence. "
    "Output JSON: {"
    "data_quality_notes: [...], "
    "selected_clusters: [{"
    "cluster_id, priority_score, reasons: [...], "
    "evidence: {ticket_count, repetitive_rate, distinct_fix_ids, top_fix_ids, has_dominant_fix, sample_ticket_ids: [...]}"
    "}], "
    "skipped: [{cluster_id, reason}]"
    "}. "
    "Every number must come from a tool result. One-sentence reason per selected cluster. Do not write articles."
)


class ClusterEvidence(BaseModel):
    ticket_count: int = Field(description="Total ticket count in this cluster")
    repetitive_rate: float = Field(description="Fraction of tickets that are repetitive (0.0 to 1.0)")
    distinct_fix_ids: list[int] = Field(description="Sorted list of distinct fix IDs on record")
    top_fix_ids: list[int] = Field(description="Fix ID(s) with highest occurrence count")
    has_dominant_fix: bool = Field(description="True if top fix share >= 30% and count >= 2")
    sample_ticket_ids: list[str] = Field(description="Sample verified ticket IDs from get_cluster_tickets")


class SelectedCluster(BaseModel):
    cluster_id: str
    priority_score: float = Field(description="Calculated priority score based on ticket volume, repetitive rate, fix spread, and severity")
    reasons: list[str] = Field(description="One-sentence reasons for selecting this cluster")
    evidence: ClusterEvidence


class SkippedCluster(BaseModel):
    cluster_id: str
    reason: str


class PatternAnalystReport(BaseModel):
    data_quality_notes: list[str] = Field(description="Observations from data_quality_report (e.g. summary vs product mismatches, SLA anomalies)")
    selected_clusters: list[SelectedCluster] = Field(description="Top N selected clusters prioritized for KB article creation")
    skipped: list[SkippedCluster] = Field(description="Clusters evaluated but not selected, with rationale")


def run_pattern_analyst(top_n: int = 6, max_steps: int = 15) -> dict:
    """
    Execute the Pattern Analyst agent to select top N candidate clusters from baseline ticket evidence.
    Returns: dict {run_id, agent, content, data, status, steps}
    """
    user_input = (
        f"Analyze support ticket patterns for the baseline window (< 2026-05-01). "
        f"Select the top N={top_n} clusters that warrant dedicated KB articles. "
        f"Follow all steps: inspect data quality, gather cluster stats, inspect fix usage, "
        f"confirm sample ticket IDs with get_cluster_tickets, and output the validated JSON report."
    )

    tools = [
        data_quality_report,
        cluster_stats,
        fix_usage_map,
        get_cluster_tickets,
    ]

    return run_agent(
        name=AGENT_NAME,
        instruction=INSTRUCTION,
        tools=tools,
        user_input=user_input,
        output_schema=PatternAnalystReport,
        max_steps=max_steps,
    )
