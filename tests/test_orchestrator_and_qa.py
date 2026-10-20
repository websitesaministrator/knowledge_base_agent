"""
tests/test_orchestrator_and_qa.py - Unit tests for Orchestrator and Q&A Agent.
Verifies:
1. Q&A agent grounds answers in article_store and returns citations & confidence.
2. Master Orchestrator executes all 5 steps in the lifecycle plan.
"""
from __future__ import annotations
import unittest
from unittest.mock import patch, MagicMock

from agents.qa_agent import answer
from agents.orchestrator import run_lifecycle_sweep
from tools.article_store import save_article
from tools.audit_log import clear_log


class TestOrchestratorAndQA(unittest.TestCase):
    def setUp(self):
        clear_log()
        self.sample_art = {
            "article_id": "KB-billing-billing-variant-3-v1",
            "cluster_id": "Billing|Billing - variant 3",
            "title": "Billing issues (variant 3): recurring tickets",
            "version": 1,
            "status": "published",
            "what_we_see": "User reported billing issues across 10 tickets.",
            "products": [{"name": "InsightHub", "count": 3}],
            "severity_mix": [{"severity": "P4", "count": 5}],
            "resolutions_on_record": [{"fix_id": 8, "ticket_count": 2, "other_categories_count": 6}],
            "suggested_triage_steps": [{"text": "Request billing invoice from user.", "label": "AI-suggested, unverified"}],
            "needs_sme_input": ["Confirm fix procedure"],
            "evidence": {"ticket_ids": ["TKT-00046"], "ticket_count": 10, "repetitive_rate": 0.9},
        }
        save_article(self.sample_art)

    @patch("agents.qa_agent.chat_json")
    def test_qa_agent_answers_with_citations(self, mock_chat_json):
        mock_chat_json.return_value = {
            "answer": "Apply standard fix #8 referencing invoice verification.",
            "citations": ["KB-billing-billing-variant-3-v1"],
            "confidence": 0.92,
            "caveat": None,
        }

        resp = answer("How to resolve billing variant 3?")
        self.assertEqual(resp.citations, ["KB-billing-billing-variant-3-v1"])
        self.assertGreaterEqual(resp.confidence, 0.9)
        self.assertIn("standard fix #8", resp.answer)

    @patch("agents.orchestrator.run_synthesis_pipeline")
    @patch("agents.orchestrator.evaluate_article_lifecycle")
    @patch("agents.orchestrator.analyze_patterns")
    def test_orchestrator_sweep_execution(self, mock_analyst, mock_lifecycle, mock_pipeline):
        mock_analyst.return_value = {"data": {"top_candidates": ["Billing|Billing - variant 3"]}}
        mock_pipeline.return_value = {
            "article_id": "KB-billing-billing-variant-3-v1",
            "final_verdict": "PASS",
            "total_rounds": 2,
            "status": "pending_review",
            "verifier_unresolved": False,
        }
        mock_lifecycle.return_value = {
            "decision": "UPDATE",
            "proposed_status": "flagged_stale",
            "proposal": {"reasons": ["Drift threshold exceeded"]},
        }

        res = run_lifecycle_sweep(cluster_id="Billing|Billing - variant 3")
        self.assertEqual(res["total_steps"], 5)
        self.assertEqual(res["completed"], 5)
        self.assertEqual(res["failed"], 0)


if __name__ == "__main__":
    unittest.main()
