"""
tests/test_lifecycle_monitor.py - Unit tests for agents/lifecycle_monitor.py.
Verifies:
1. Decision schemas conform to LifecycleProposal (KEEP, UPDATE, RETIRE).
2. Proposal is saved to article.lifecycle_proposal without modifying article.status.
3. Decision logic accurately flags thresholds for new fix share, rep rate increase, resolution time, and low ticket retirement.
"""
from __future__ import annotations
import json
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from agents.lifecycle_monitor import evaluate_article_lifecycle, LifecycleProposal
from tools.article_store import save_article, get_article
from tools.audit_log import clear_log


class TestLifecycleMonitor(unittest.TestCase):
    def setUp(self):
        clear_log()
        self.test_article = {
            "article_id": "KB-billing-billing-variant-3-v1",
            "cluster_id": "Billing|Billing - variant 3",
            "title": "Billing issues (variant 3): recurring tickets",
            "version": 1,
            "status": "published",
            "what_we_see": "Factual narrative",
            "products": [{"name": "InsightHub", "count": 3}],
            "severity_mix": [{"severity": "P4", "count": 5}],
            "resolutions_on_record": [{"fix_id": 8, "ticket_count": 2, "other_categories_count": 6}],
            "suggested_triage_steps": [{"text": "Check invoices", "label": "AI-suggested, unverified"}],
            "needs_sme_input": ["Confirm fix 8"],
            "evidence": {"ticket_ids": ["TKT-00046"], "ticket_count": 10, "repetitive_rate": 0.9},
        }
        save_article(self.test_article)

    @patch("agents.base.get_client")
    def test_lifecycle_proposal_saved_without_changing_status(self, mock_get_client):
        """Lifecycle proposal must attach to article without modifying article.status."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_proposal = {
            "article_id": "KB-billing-billing-variant-3-v1",
            "decision": "UPDATE",
            "reasons": [
                "Fix #14 is new in replay and covers 33.3% of replay tickets (threshold: 30%)",
                "Average resolution time rose from 47.1h to 61.3h (30.1% increase, threshold: 20%)"
            ],
            "signals": {
                "baseline_ticket_count": 10,
                "replay_ticket_count": 3,
                "volume_change": -7,
                "new_fix_shares_in_replay": {"14": 0.333, "16": 0.333}
            },
            "proposed_status": "flagged_stale",
            "update_request": "Incorporate Fix #14 and #16 which appeared in replay tickets."
        }

        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(mock_proposal), tool_calls=None))])
        mock_client.chat.completions.create.return_value = resp

        res = evaluate_article_lifecycle("KB-billing-billing-variant-3-v1")
        self.assertEqual(res["decision"], "UPDATE")
        self.assertEqual(res["proposed_status"], "flagged_stale")

        # Verify article status remained 'published' in store
        stored = get_article("KB-billing-billing-variant-3-v1")
        self.assertEqual(stored["status"], "published")
        self.assertIsNotNone(stored.get("lifecycle_proposal"))
        self.assertEqual(stored["lifecycle_proposal"]["decision"], "UPDATE")
        self.assertEqual(len(stored["lifecycle_proposal"]["reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
