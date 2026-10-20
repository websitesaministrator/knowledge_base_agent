"""
scripts/run_demo_analyst_author.py - End-to-end demo of Part B (Pattern Analyst) and Part C (KB Author).
Runs under Python 3.11 venv with OpenAI LLM and deterministic tools.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.pattern_analyst import run_pattern_analyst
from agents.kb_author import draft_article
from tools.article_store import get_article, list_articles


def main():
    print("=" * 80)
    print("DEMO: PART B — PATTERN ANALYST AGENT")
    print("=" * 80)

    print("\nRunning Pattern Analyst (top_n=6) on baseline tickets (< 2026-05-01)...")
    analyst_result = run_pattern_analyst(top_n=6, max_steps=12)

    print(f"Analyst Run ID: {analyst_result['run_id']}")
    print(f"Status:         {analyst_result['status']}")
    print(f"Steps:          {analyst_result['steps']}")

    analyst_data = analyst_result.get("data")
    if not analyst_data:
        print("\n[ERROR] Pattern Analyst did not return structured data!")
        print("Raw Content:", analyst_result.get("content"))
        return

    print("\n--- Data Quality Notes Identified by Agent ---")
    for note in analyst_data.get("data_quality_notes", []):
        print(f" * {note}")

    print("\n--- Selected Clusters (Prioritized for KB Creation) ---")
    selected = analyst_data.get("selected_clusters", [])
    for idx, sc in enumerate(selected, start=1):
        ev = sc["evidence"]
        print(f"\n{idx}. Cluster: {sc['cluster_id']} (Priority Score: {sc['priority_score']})")
        print(f"   Reasons:           {'; '.join(sc['reasons'])}")
        print(f"   Ticket Count:      {ev['ticket_count']}")
        print(f"   Repetitive Rate:   {ev['repetitive_rate'] * 100:.1f}%")
        print(f"   Distinct Fix IDs:  {ev['distinct_fix_ids']}")
        print(f"   Top Fix IDs:       {ev['top_fix_ids']} (Has Dominant Fix: {ev['has_dominant_fix']})")
        print(f"   Sample Tickets:    {ev['sample_ticket_ids']}")

    if not selected:
        print("No clusters were selected!")
        return

    top_cluster_id = selected[0]["cluster_id"]
    print("\n" + "=" * 80)
    print(f"DEMO: PART C — KB AUTHOR AGENT FOR TOP CLUSTER: '{top_cluster_id}'")
    print("=" * 80)

    print(f"\nDrafting KB article for '{top_cluster_id}'...")
    author_result = draft_article(cluster_id=top_cluster_id, version=1, window="baseline", max_steps=8)

    print(f"Author Run ID:  {author_result['run_id']}")
    print(f"Status:         {author_result['status']}")
    print(f"Steps:          {author_result['steps']}")

    article = author_result.get("article")
    if not article and author_result.get("data"):
        article = author_result["data"]

    if not article:
        print("\n[ERROR] KB Author did not produce an article!")
        print("Raw Content:", author_result.get("content"))
        return

    # Verify article is accessible via article_store
    stored = get_article(article["article_id"])
    print(f"\nVerification in article_store: {'FOUND & VERIFIED' if stored else 'NOT FOUND'}")
    print(f"Article ID: {article['article_id']}")

    print("\n--- GENERATED KB ARTICLE (EVIDENCE-GROUNDED) ---")
    print(json.dumps(article, indent=2))
    print("=" * 80)


if __name__ == "__main__":
    main()
