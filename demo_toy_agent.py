"""
demo_toy_agent.py - Demonstrates run_agent with a real pandas tool on ps03_support_tickets.csv.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
import pandas as pd
from pydantic import BaseModel, Field

from agents.base import run_agent
from tools.audit_log import get_all_entries, clear_log

CSV_PATH = Path("data/ps03_support_tickets.csv")


def cluster_stats(category: str, subcategory: str) -> dict:
    """
    Compute cluster metrics for a given category and subcategory from the support tickets dataset.
    Returns total_tickets, repetitive_rate, distinct_fix_ids, top_fix_id, top_fix_share, and mean_resolution_time_hrs.
    """
    df = pd.read_csv(CSV_PATH)
    filtered = df[(df["category"].str.strip().str.lower() == category.strip().lower()) &
                  (df["subcategory"].str.strip().str.lower() == subcategory.strip().lower())]

    if filtered.empty:
        return {"error": f"No tickets found for category='{category}' and subcategory='{subcategory}'"}

    total = len(filtered)
    repetitive_count = int(filtered["is_repetitive"].fillna(False).sum())
    rep_rate = round(repetitive_count / total, 3)

    # Extract standard fix numbers e.g. "fix #11"
    fix_ids = []
    for note in filtered["resolution_notes"].dropna():
        match = re.search(r"fix\s*#?(\d+)", str(note), re.IGNORECASE)
        if match:
            fix_ids.append(int(match.group(1)))

    fix_series = pd.Series(fix_ids)
    top_fix_id = int(fix_series.mode()[0]) if not fix_series.empty else None
    top_fix_share = round((fix_series == top_fix_id).mean(), 3) if top_fix_id is not None else 0.0

    return {
        "category": category,
        "subcategory": subcategory,
        "total_tickets": total,
        "repetitive_count": repetitive_count,
        "repetitive_rate": rep_rate,
        "distinct_fix_ids": sorted(list(set(fix_ids))),
        "top_fix_id": top_fix_id,
        "top_fix_share": top_fix_share,
        "mean_resolution_time_hrs": round(float(filtered["resolution_time_hrs"].mean()), 1),
    }


class ClusterAnalysisReport(BaseModel):
    category: str
    subcategory: str
    total_tickets: int
    repetitive_rate: float
    distinct_fix_count: int
    top_fix_id: int
    recommendation: str = Field(description="Recommendation whether to synthesize a KB article (e.g. CREATE_KB, MONITOR, IGNORE)")
    rationale: str = Field(description="Evidence-based reasoning for the recommendation")


def main():
    print("=" * 60)
    print("DEMO: Running Toy Agent with Real Pandas Tool on CSV")
    print("=" * 60)

    clear_log()

    prompt = (
        "Please analyze the support ticket cluster for category='Access Control' and "
        "subcategory='Access Control - variant 3'. Call the cluster_stats tool to retrieve "
        "real evidence, then provide a structured ClusterAnalysisReport in JSON."
    )

    instruction = (
        "You are an expert Pattern Analyst agent for IT support knowledge bases. "
        "Use the cluster_stats tool to inspect evidence. "
        "Return your final response strictly as a JSON object adhering to the schema."
    )

    result = run_agent(
        name="PatternAnalystToy",
        instruction=instruction,
        tools=[cluster_stats],
        user_input=prompt,
        output_schema=ClusterAnalysisReport,
        max_steps=5,
    )

    print("\n--- Agent Execution Summary ---")
    print(f"Run ID:  {result['run_id']}")
    print(f"Status:  {result['status']}")
    print(f"Steps:   {result['steps']}")

    print("\n--- Structured Output (Pydantic Validated) ---")
    print(json.dumps(result["data"], indent=2))

    print("\n--- Audit Log Entries Generated ---")
    entries = get_all_entries()
    for e in entries:
        print(f"[{e['timestamp']}] Agent: {e['agent']} | Action: {e['action']} | Outcome: {e['outcome']}")
        if "result" in e.get("outputs", {}):
            print(f"   Outputs: {e['outputs']['result'][:120]}...")
    print("=" * 60)


if __name__ == "__main__":
    main()
