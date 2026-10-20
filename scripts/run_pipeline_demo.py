"""
scripts/run_pipeline_demo.py - Runs the synthesis pipeline loop (Author -> Verifier)
on 'Billing|Billing - variant 3' and prints round verdicts, revisions, and audit log entries.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.kb_pipeline import run_synthesis_pipeline
from tools.audit_log import get_all_entries, clear_log
from tools.article_store import get_article


def main():
    print("=" * 80)
    print("DEMO: AUTHOR -> VERIFIER PIPELINE LOOP ON 'Billing|Billing - variant 3'")
    print("=" * 80)

    clear_log()

    cluster_id = "Billing|Billing - variant 3"
    print(f"\nStarting pipeline for cluster: '{cluster_id}'...")
    result = run_synthesis_pipeline(cluster_id=cluster_id, version=1, window="baseline", max_revisions=2)

    print("\n--- Pipeline Execution Summary ---")
    print(f"Cluster ID:       {result['cluster_id']}")
    print(f"Article ID:       {result['article_id']}")
    print(f"Total Rounds:     {result['total_rounds']}")
    print(f"Final Verdict:    {result['final_verdict']}")
    print(f"Status:           {result['status']}")
    print(f"Unresolved Flag:  {result['verifier_unresolved']}")

    print("\n--- Round-by-Round Breakdown ---")
    for r in result["rounds"]:
        print(f"\n[Round {r['round']}] Verdict: {r['verdict']}")
        if r.get("issues"):
            print(f"  Issues found ({len(r['issues'])}):")
            for i, iss in enumerate(r["issues"], start=1):
                print(f"    {i}. [{iss.get('check')}] {iss.get('detail')}")
                print(f"       Action: {iss.get('how_to_fix')}")
        else:
            print("  Zero issues! Article passed verification.")

    print("\n" + "=" * 80)
    print("FINAL ARTICLE PRODUCED")
    print("=" * 80)
    article = get_article(result["article_id"])
    print(json.dumps(article, indent=2))

    print("\n" + "=" * 80)
    print("AUDIT LOG LINES GENERATED")
    print("=" * 80)
    entries = get_all_entries()
    for e in entries:
        print(f"[{e['timestamp']}] Agent: {e['agent']:<15} | Action: {e['action']:<25} | Outcome: {e['outcome']}")
        if "verdict" in e.get("outputs", {}):
            print(f"   Outputs: verdict={e['outputs']['verdict']}, issues_count={e['outputs'].get('issue_count')}")
        elif "result" in e.get("outputs", {}):
            print(f"   Outputs: {str(e['outputs']['result'])[:100]}...")
    print("=" * 80)


if __name__ == "__main__":
    main()
