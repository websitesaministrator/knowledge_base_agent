"""
scripts/run_pipeline_demo.py - Runs the synthesis pipeline loop (Author -> Verifier)
on 'Billing|Billing - variant 3' and prints round verdicts, revisions, and audit log entries.
Compares final synthesized article numbers with pandas ground truth.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pandas as pd

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.kb_pipeline import run_synthesis_pipeline
from tools.audit_log import get_all_entries, clear_log
from tools.article_store import get_article
from tools.tickets import get_cluster_tickets, fix_usage_map


def print_comparison_with_pandas(article: dict, cluster_id: str, window: str = "baseline"):
    print("\n" + "=" * 80)
    print(f"PANDAS GROUND TRUTH VS FINAL ARTICLE COMPARISON ({cluster_id})")
    print("=" * 80)

    # Load pandas ground truth
    tickets_res = get_cluster_tickets(cluster_id, window=window, limit=500)
    df = pd.DataFrame(tickets_res.get("tickets", []))

    # 1. Product counts comparison
    gt_products = df["product"].value_counts().to_dict()
    art_products = {p["name"]: p["count"] for p in article.get("products", [])}
    all_prod_keys = sorted(set(gt_products.keys()) | set(art_products.keys()))

    print("\n[1. Product Counts]")
    print(f"{'Product':<20} | {'Pandas GT':<10} | {'Article':<10} | {'Match?':<6}")
    print("-" * 52)
    for p in all_prod_keys:
        gt_c = gt_products.get(p, 0)
        art_c = art_products.get(p, 0)
        match = "PASS" if gt_c == art_c else "FAIL"
        print(f"{p:<20} | {gt_c:<10} | {art_c:<10} | {match:<6}")

    # 2. Severity mix comparison
    gt_sev = df["severity"].value_counts().to_dict()
    art_sev = {s["severity"]: s["count"] for s in article.get("severity_mix", [])}
    all_sev_keys = sorted(set(gt_sev.keys()) | set(art_sev.keys()))

    print("\n[2. Severity Mix]")
    print(f"{'Severity':<20} | {'Pandas GT':<10} | {'Article':<10} | {'Match?':<6}")
    print("-" * 52)
    for s in all_sev_keys:
        gt_c = gt_sev.get(s, 0)
        art_c = art_sev.get(s, 0)
        match = "PASS" if gt_c == art_c else "FAIL"
        print(f"{s:<20} | {gt_c:<10} | {art_c:<10} | {match:<6}")

    # 3. Overall Evidence comparison
    gt_t_count = len(df)
    gt_rep_count = int(df["is_repetitive"].sum())
    gt_rep_rate = round(gt_rep_count / gt_t_count, 3) if gt_t_count else 0.0

    art_ev = article.get("evidence", {})
    art_t_count = art_ev.get("ticket_count")
    art_rep_rate = art_ev.get("repetitive_rate")

    print("\n[3. Cluster Evidence Metrics]")
    print(f"{'Metric':<25} | {'Pandas GT':<10} | {'Article':<10} | {'Match?':<6}")
    print("-" * 57)
    print(f"{'Total Tickets':<25} | {gt_t_count:<10} | {str(art_t_count):<10} | {'PASS' if gt_t_count == art_t_count else 'FAIL':<6}")
    print(f"{'Repetitive Rate':<25} | {gt_rep_rate:<10} | {str(art_rep_rate):<10} | {'PASS' if abs(gt_rep_rate - float(art_rep_rate or 0)) < 0.01 else 'FAIL':<6}")

    # 4. Resolutions on record comparison
    fum = fix_usage_map().get("fixes", {})
    actual_fix_counts = df["fix_id"].dropna().astype(int).value_counts().to_dict()
    art_fixes = {r["fix_id"]: r for r in article.get("resolutions_on_record", [])}
    all_fixes = sorted(set(actual_fix_counts.keys()) | set(art_fixes.keys()))

    print("\n[4. Fixes & Other Categories Counts]")
    print(f"{'Fix ID':<8} | {'GT Count':<10} | {'Art Count':<10} | {'GT Other Cats':<14} | {'Art Other Cats':<14} | {'Match?':<6}")
    print("-" * 75)
    for f in all_fixes:
        gt_fc = actual_fix_counts.get(f, 0)
        total_cats = fum.get(str(f), {}).get("category_count", 0)
        gt_other = max(0, total_cats - 1)

        art_rec = art_fixes.get(f, {})
        art_fc = art_rec.get("ticket_count", 0)
        art_other = art_rec.get("other_categories_count", 0)

        match = "PASS" if (gt_fc == art_fc and gt_other == art_other) else "FAIL"
        print(f"Fix #{f:<4} | {gt_fc:<10} | {art_fc:<10} | {gt_other:<14} | {art_other:<14} | {match:<6}")
    print("=" * 80)


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
        if "issue_count" in e.get("outputs", {}):
            print(f"   Outputs: issues_count={e['outputs']['issue_count']}, passed={e['outputs'].get('passed')}")
        elif "verdict" in e.get("outputs", {}):
            print(f"   Outputs: verdict={e['outputs']['verdict']}, issues_count={e['outputs'].get('issue_count')}")

    # Compare with pandas ground truth
    if article:
        print_comparison_with_pandas(article, cluster_id, window="baseline")


if __name__ == "__main__":
    main()
