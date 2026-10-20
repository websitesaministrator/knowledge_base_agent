"""
Unit tests for agents/verifier.py and agents/kb_pipeline.py.
Verifies:
1. A draft with a fake ticket ID gets REVISE.
2. The pipeline loop stops after 2 revisions if issues persist, setting verifier_unresolved=True.
"""
from __future__ import annotations
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents.verifier import verify_article, VerifierVerdict
from agents.kb_pipeline import run_synthesis_pipeline
from tools.audit_log import clear_log, get_all_entries


class TestVerifierAndPipeline(unittest.TestCase):
    def setUp(self):
        clear_log()

    @patch("agents.base.get_client")
    def test_draft_with_fake_ticket_gets_revise(self, mock_get_client):
        """A draft containing a non-existent ticket ID must receive a REVISE verdict."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock Verifier returning REVISE with fake ticket issue
        verdict_json = json.dumps({
            "verdict": "REVISE",
            "issues": [
                {
                    "check": "check_ticket_ids",
                    "detail": "Ticket ID 'TKT-99999' does not exist in cluster tickets.",
                    "how_to_fix": "Remove 'TKT-99999' and cite only verified tickets."
                }
            ]
        })
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=verdict_json, tool_calls=None))])
        mock_client.chat.completions.create.return_value = resp

        fake_draft = {
            "article_id": "KB-test-cluster-v1",
            "cluster_id": "Billing|Billing - variant 3",
            "title": "Test Title",
            "version": 1,
            "status": "draft",
            "what_we_see": "Reported in TKT-99999.",
            "resolutions_on_record": [{"fix_id": 8, "ticket_count": 1}],
            "suggested_steps": [{"text": "Check invoices.", "label": "AI-suggested, unverified"}],
            "needs_sme_input": ["Verify fix #8."],
            "evidence": {"ticket_ids": ["TKT-99999"], "ticket_count": 1, "repetitive_rate": 1.0},
        }

        result = verify_article(fake_draft, cluster_id="Billing|Billing - variant 3")
        self.assertEqual(result["verdict"], "REVISE")
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["check"], "check_ticket_ids")

    @patch("agents.kb_pipeline.draft_article")
    @patch("agents.kb_pipeline.verify_article")
    def test_pipeline_stops_at_max_revisions(self, mock_verify, mock_draft):
        """Pipeline must perform initial draft + max 2 revisions (3 rounds total) then flag verifier_unresolved."""
        mock_draft.return_value = {
            "status": "success",
            "data": {
                "article_id": "KB-billing-billing-variant-3-v1",
                "cluster_id": "Billing|Billing - variant 3",
                "title": "Billing Draft",
                "version": 1,
                "status": "draft",
                "what_we_see": "Draft content",
                "resolutions_on_record": [{"fix_id": 8, "ticket_count": 2}],
                "suggested_steps": [{"text": "Check invoices", "label": "AI-suggested, unverified"}],
                "needs_sme_input": ["Confirm fix"],
                "evidence": {"ticket_ids": ["TKT-00046"], "ticket_count": 1, "repetitive_rate": 0.9},
            }
        }

        # Verifier always returns REVISE
        mock_verify.return_value = {
            "verdict": "REVISE",
            "issues": [
                {
                    "check": "check_required_statements",
                    "detail": "Missing product mismatch note.",
                    "how_to_fix": "Add note on 242/300 product mismatch."
                }
            ],
            "run_id": "v_test",
            "steps": 1,
        }

        pipeline_result = run_synthesis_pipeline(
            cluster_id="Billing|Billing - variant 3",
            version=1,
            max_revisions=2,
        )

        # Total rounds must be exactly 3 (1 initial + 2 revisions)
        self.assertEqual(pipeline_result["total_rounds"], 3)
        self.assertEqual(mock_draft.call_count, 3)
        self.assertEqual(mock_verify.call_count, 3)
        self.assertEqual(pipeline_result["final_verdict"], "REVISE")
        self.assertTrue(pipeline_result["verifier_unresolved"])
        self.assertEqual(pipeline_result["status"], "pending_review")

        # Verify audit log recorded all 3 rounds
        entries = get_all_entries()
        round_actions = [e["action"] for e in entries if "verify_round" in e["action"]]
        self.assertEqual(round_actions, ["verify_round:1", "verify_round:2", "verify_round:3"])


if __name__ == "__main__":
    unittest.main()
