"""
scripts/run_lifecycle_demo.py - Live demo of Lifecycle Monitor agent.
1. Evaluates published KB article for 'Billing|Billing - variant 3' against replay tickets.
2. Displays the proposed decision (KEEP/UPDATE/RETIRE), justification with numbers, and signals.
3. Confirms article status remains 'published' and proposal is saved in lifecycle_proposal.
4. Computes and displays the decision spread (KEEP/UPDATE/RETIRE) across all 24 clusters' signals.
"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from agents.lifecycle_monitor import evaluate_article_lifecycle
from tools.article_store import get_article, save_article
from tools.tickets import compute_cluster_signals, cluster_stats
from tools.audit_log import get_all_entries, clear_log


def evaluate_cluster_drift(cluster_id: str) -> dict:
    """Evaluate drift signals for a cluster using starting defaults from config.py."""
    sig = compute_cluster_signals(cluster_id)
    r_vol = sig.get("replay_ticket_count", 0)
    b_vol = sig.get("baseline_ticket_count", 0)
    b_hrs = sig.get("baseline_avg_resolution_hrs", 0.0)
    r_hrs = sig.get("replay_avg_resolution_hrs", 0.0)
    pct_hrs = ((r_hrs - b_hrs) / b_hrs) if b_hrs > 0 else 0.0
    r_rep = sig.get("replay_repetitive_rate", 0.0)
    b_rep = sig.get("baseline_repetitive_rate", 0.0)

    new_shares = sig.get("new_fix_shares_in_replay", {})
    max_new_fix_share = max(new_shares.values()) if new_shares else 0.0

    if r_vol <= settings.lifecycle_retire_ticket_threshold:
        decision = "RETIRE"
        reasons = [f"Replay ticket volume is {r_vol} (<= {settings.lifecycle_retire_ticket_threshold})"]
    elif (
        (max_new_fix_share >= settings.lifecycle_new_fix_share_threshold)
        or (r_rep >= settings.lifecycle_rep_rate_threshold and r_rep > b_rep)
        or (pct_hrs >= settings.lifecycle_avg_res_increase_threshold)
    ):
        decision = "UPDATE"
        reasons = []
        if max_new_fix_share >= settings.lifecycle_new_fix_share_threshold:
            new_fix_items = [f"Fix #{fid} ({share:.1%})" for fid, share in new_shares.items() if share >= settings.lifecycle_new_fix_share_threshold]
            reasons.append(f"New fix in replay >= {settings.lifecycle_new_fix_share_threshold:.0%}: {', '.join(new_fix_items)}")
        if r_rep >= settings.lifecycle_rep_rate_threshold and r_rep > b_rep:
            reasons.append(f"Repetitive rate rose to {r_rep:.2f} (baseline was {b_rep:.2f}, threshold: {settings.lifecycle_rep_rate_threshold:.2f})")
        if pct_hrs >= settings.lifecycle_avg_res_increase_threshold:
            reasons.append(f"Average resolution time rose {pct_hrs:.1%} from {b_hrs:.1f}h to {r_hrs:.1f}h (threshold: {settings.lifecycle_avg_res_increase_threshold:.0%})")
    else:
        decision = "KEEP"
        reasons = [f"Replay volume is {r_vol} tickets; no drift thresholds exceeded"]

    return {
        "cluster_id": cluster_id,
        "decision": decision,
        "reasons": reasons,
        "signals": sig,
    }


def main():
    print("=" * 80)
    print("DEMO: LIFECYCLE MONITOR EVALUATION ON PUBLISHED KB ARTICLE")
    print("=" * 80)

    clear_log()
    article_id = "KB-billing-billing-variant-3-v1"

    # Step 1: Ensure test article is published in store
    art = get_article(article_id)
    if not art:
        print(f"Article {article_id} not found in store; initializing baseline draft...")
        art = {
            "article_id": article_id,
            "cluster_id": "Billing|Billing - variant 3",
            "title": "Billing issues (variant 3): recurring tickets",
            "version": 1,
            "status": "published",
            "what_we_see": "Factual narrative on billing tickets.",
            "products": [
                {"name": "InsightHub", "count": 3},
                {"name": "CloudSync", "count": 2},
                {"name": "DataConnect", "count": 2},
                {"name": "PlatformX", "count": 2},
                {"name": "AnalyticsPro", "count": 1},
            ],
            "severity_mix": [
                {"severity": "P4", "count": 5},
                {"severity": "P2", "count": 3},
                {"severity": "P3", "count": 1},
                {"severity": "P1", "count": 1},
            ],
            "resolutions_on_record": [
                {"fix_id": 8, "ticket_count": 2, "other_categories_count": 6},
                {"fix_id": 19, "ticket_count": 1, "other_categories_count": 6},
                {"fix_id": 1, "ticket_count": 1, "other_categories_count": 5},
            ],
            "suggested_triage_steps": [
                {"text": "Request user invoice copy.", "label": "AI-suggested, unverified"}
            ],
            "needs_sme_input": ["Confirm fix procedure"],
            "evidence": {"ticket_ids": ["TKT-00046"], "ticket_count": 10, "repetitive_rate": 0.9},
        }
    art["status"] = "published"
    save_article(art)

    print(f"\nEvaluating published article: '{article_id}' (Cluster: {art['cluster_id']})")
    result = evaluate_article_lifecycle(article_id=article_id, max_steps=8)

    print("\n--- Lifecycle Monitor Decision ---")
    print(f"Article ID:        {result.get('article_id')}")
    print(f"Cluster ID:        {result.get('cluster_id')}")
    print(f"Current Status:    {result.get('current_status')} (UNCHANGED)")
    print(f"Decision:          {result.get('decision')}")
    print(f"Proposed Status:   {result.get('proposed_status')}")

    proposal = result.get("proposal") or {}
    print("\n--- Justification (with exact numbers from tools) ---")
    for r in proposal.get("reasons", []):
        print(f"  * {r}")

    if proposal.get("update_request"):
        print(f"\nUpdate Request:\n  {proposal.get('update_request')}")

    stored_art = get_article(article_id)
    print("\n--- Verification of Storage Invariant ---")
    print(f"Stored article status in DB: {stored_art.get('status')} (Must be 'published')")
    print(f"lifecycle_proposal present:  {'lifecycle_proposal' in stored_art}")

    # Step 2: Compute and report spread across all 24 clusters
    print("\n" + "=" * 80)
    print("DRIFT SIGNALS & LIFECYCLE DECISION SPREAD ACROSS ALL 24 CLUSTERS")
    print("=" * 80)

    cs = cluster_stats(window="all")["clusters"]
    all_clusters = sorted(list(cs.keys()))

    cluster_evals = [evaluate_cluster_drift(cid) for cid in all_clusters]
    counts = Counter(e["decision"] for e in cluster_evals)

    print(f"\nSummary of Decisions across {len(all_clusters)} clusters:")
    print(f"{'Decision':<12} | {'Count':<8} | {'Percentage':<10}")
    print("-" * 34)
    for dec in ["UPDATE", "KEEP", "RETIRE"]:
        cnt = counts.get(dec, 0)
        pct = (cnt / len(all_clusters)) * 100
        print(f"{dec:<12} | {cnt:<8} | {pct:>5.1f}%")

    print("\nDetailed Cluster Breakdown:")
    print(f"{'Cluster':<38} | {'Decision':<8} | {'Justification / Drift Numbers'}")
    print("-" * 90)
    for e in cluster_evals:
        reasons_str = "; ".join(e["reasons"])
        print(f"{e['cluster_id']:<38} | {e['decision']:<8} | {reasons_str}")

    print("\n" + "=" * 80)
    print("AUDIT LOG ENTRIES")
    print("=" * 80)
    for entry in get_all_entries():
        if entry["agent"] == "LifecycleMonitor":
            print(f"[{entry['timestamp']}] Agent: {entry['agent']} | Action: {entry['action']} | Decision: {entry['outputs'].get('decision')} | Proposed: {entry['outputs'].get('proposed_status')}")
    print("=" * 80)


if __name__ == "__main__":
    main()
