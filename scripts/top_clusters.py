"""
scripts/top_clusters.py - Prints top 5 clusters by ticket_count for the baseline window.
Uses deterministic pandas tools from tools/tickets.py.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.tickets import cluster_stats


def main():
    print("=" * 80)
    print("TOP 5 SUPPORT TICKET CLUSTERS - BASELINE WINDOW (< 2026-05-01)")
    print("=" * 80)

    stats = cluster_stats(window="baseline")
    clusters = stats["clusters"]

    # Sort by ticket_count descending, then repetitive_rate descending
    sorted_clusters = sorted(
        clusters.values(),
        key=lambda c: (c["ticket_count"], c["repetitive_rate"]),
        reverse=True
    )

    top_5 = sorted_clusters[:5]

    header = f"{'Rank':<5} | {'Cluster ID':<42} | {'Count':<5} | {'Rep %':<7} | {'Top Fix':<8} | {'Fix Share':<9} | {'Avg Hrs':<7}"
    print(header)
    print("-" * 92)

    for i, c in enumerate(top_5, start=1):
        top_fix_str = f"#{c['top_fix_id']}" if c['top_fix_id'] is not None else "N/A"
        fix_share_str = f"{c['top_fix_share'] * 100:.1f}%" if c['top_fix_share'] else "0.0%"
        rep_rate_str = f"{c['repetitive_rate'] * 100:.1f}%"
        print(
            f"{i:<5} | {c['cluster_id']:<42} | {c['ticket_count']:<5} | {rep_rate_str:<7} | "
            f"{top_fix_str:<8} | {fix_share_str:<9} | {c['avg_resolution_hrs']:<7.1f}"
        )

    print("=" * 80)
    print(f"Total tickets in baseline window: {stats['total_tickets']} (across {stats['total_clusters']} clusters)")
    print("=" * 80)


if __name__ == "__main__":
    main()
