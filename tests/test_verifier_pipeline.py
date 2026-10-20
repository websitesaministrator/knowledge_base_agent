"""
tests/test_verifier_pipeline.py - Unit tests for agents/verifier.py and agents/kb_pipeline.py.
Verifies:
1. Swapped product counts -> REVISE with expected values
2. Total-instead-of-other count -> REVISE with expected values
3. Step like 'Reset the billing module' -> REVISE
4. Draft with non-existent ticket ID -> REVISE
5. Pipeline stops after max revisions if issues persist
"""
from __future__ import annotations
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents.verifier import verify_article, run_code_checks, VerifierVerdict
from agents.kb_pipeline import run_synthesis_pipeline
from tools.audit_log import clear_log, get_all_entries


BASE_VALID_DRAFT = {
    "article_id": "KB-billing-billing-variant-3-v1",
    "cluster_id": "Billing|Billing - variant 3",
    "title": "Billing issues (variant 3): recurring tickets",
    "version": 1,
    "status": "draft",
    "what_we_see": (
        "Ticket summaries contain only templated category wording ('User reported billing while using X') with no symptom detail. "
        "In 242/300 rows across the dataset, the product in the summary differs from the product column. "
        "In this cluster of 10 tickets, 9 are repetitive (repetitive rate 0.9). Standard fix procedures are not present in data."
    ),
    "products": [
        {"name": "InsightHub", "count": 3},
        {"name": "DataConnect", "count": 2},
        {"name": "CloudSync", "count": 2},
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
        {"fix_id": 12, "ticket_count": 1, "other_categories_count": 6},
        {"fix_id": 5, "ticket_count": 1, "other_categories_count": 7},
        {"fix_id": 4, "ticket_count": 1, "other_categories_count": 7},
        {"fix_id": 7, "ticket_count": 1, "other_categories_count": 7},
        {"fix_id": 20, "ticket_count": 1, "other_categories_count": 4},
        {"fix_id": 6, "ticket_count": 1, "other_categories_count": 6},
    ],
    "suggested_triage_steps": [
        {"text": "Request billing invoice and transaction timestamp from user.", "label": "AI-suggested, unverified"},
        {"text": "Lookup prior tickets for customer account in support portal.", "label": "AI-suggested, unverified"},
        {"text": "Consult recorded resolution notes for standard fix references.", "label": "AI-suggested, unverified"},
        {"text": "Escalate to Finance SME if invoice discrepancies persist.", "label": "AI-suggested, unverified"},
    ],
    "needs_sme_input": ["Confirm standard fix procedure steps for Fix #8."],
    "evidence": {
        "ticket_ids": [
            "TKT-00046", "TKT-00081", "TKT-00107", "TKT-00172", "TKT-00199",
            "TKT-00216", "TKT-00219", "TKT-00225", "TKT-00244", "TKT-00247"
        ],
        "ticket_count": 10,
        "repetitive_rate": 0.9,
    },
}


class TestVerifierAndPipeline(unittest.TestCase):
    def setUp(self):
        clear_log()

    @patch("agents.base.get_client")
    def test_swapped_product_counts_revise(self, mock_get_client):
        """Swapped product counts (AnalyticsPro 2, PlatformX 1) must return REVISE with expected values."""
        draft = copy.deepcopy(BASE_VALID_DRAFT)
        draft["products"] = [
            {"name": "InsightHub", "count": 3},
            {"name": "DataConnect", "count": 2},
            {"name": "CloudSync", "count": 2},
            {"name": "PlatformX", "count": 1},
            {"name": "AnalyticsPro", "count": 2},
        ]

        result = verify_article(draft, cluster_id="Billing|Billing - variant 3", skip_llm=True)
        self.assertEqual(result["verdict"], "REVISE")
        prod_issues = [i for i in result["issues"] if i["check"] == "check_product_counts"]
        self.assertTrue(len(prod_issues) > 0)
        self.assertIn("AnalyticsPro", prod_issues[0]["detail"])
        self.assertIn("PlatformX", prod_issues[0]["detail"])
        self.assertIn("expected", prod_issues[0]["detail"])

    @patch("agents.base.get_client")
    def test_total_instead_of_other_count_revise(self, mock_get_client):
        """Total categories count instead of other categories count must return REVISE with expected value."""
        draft = copy.deepcopy(BASE_VALID_DRAFT)
        # Fix #4 is used in 8 categories total, so other_categories_count must be 7. Set to 8:
        for r in draft["resolutions_on_record"]:
            if r["fix_id"] == 4:
                r["other_categories_count"] = 8

        result = verify_article(draft, cluster_id="Billing|Billing - variant 3", skip_llm=True)
        self.assertEqual(result["verdict"], "REVISE")
        fix_issues = [i for i in result["issues"] if i["check"] == "check_resolutions_on_record"]
        self.assertTrue(len(fix_issues) > 0)
        self.assertIn("Fix #4", fix_issues[0]["detail"])
        self.assertIn("expected 7", fix_issues[0]["detail"])

    @patch("agents.base.get_client")
    def test_system_altering_step_revise(self, mock_get_client):
        """A triage step like 'Reset the billing module' must return REVISE."""
        draft = copy.deepcopy(BASE_VALID_DRAFT)
        draft["suggested_triage_steps"].append({
            "text": "Reset the billing module to restore service.",
            "label": "AI-suggested, unverified"
        })

        result = verify_article(draft, cluster_id="Billing|Billing - variant 3", skip_llm=True)
        self.assertEqual(result["verdict"], "REVISE")
        step_issues = [i for i in result["issues"] if i["check"] == "check_suggested_triage_steps"]
        self.assertTrue(len(step_issues) > 0)
        self.assertIn("Reset", step_issues[0]["detail"])

    @patch("agents.base.get_client")
    def test_draft_with_fake_ticket_gets_revise(self, mock_get_client):
        """A draft containing a non-existent ticket ID must receive a REVISE verdict."""
        draft = copy.deepcopy(BASE_VALID_DRAFT)
        draft["evidence"]["ticket_ids"].append("TKT-99999")

        result = verify_article(draft, cluster_id="Billing|Billing - variant 3", skip_llm=True)
        self.assertEqual(result["verdict"], "REVISE")
        ticket_issues = [i for i in result["issues"] if i["check"] == "check_ticket_ids"]
        self.assertTrue(len(ticket_issues) > 0)
        self.assertIn("TKT-99999", ticket_issues[0]["detail"])

    @patch("agents.base.get_client")
    def test_valid_draft_passes_code_checks(self, mock_get_client):
        """A completely accurate draft passes deterministic code checks with 0 issues."""
        draft = copy.deepcopy(BASE_VALID_DRAFT)
        issues = run_code_checks(draft, cluster_id="Billing|Billing - variant 3")
        self.assertEqual(len(issues), 0)

        result = verify_article(draft, cluster_id="Billing|Billing - variant 3", skip_llm=True)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(len(result["issues"]), 0)

    @patch("agents.kb_pipeline.draft_article")
    @patch("agents.kb_pipeline.verify_article")
    def test_pipeline_stops_at_max_revisions(self, mock_verify, mock_draft):
        """Pipeline must perform initial draft + max 2 revisions (3 rounds total) then flag verifier_unresolved."""
        mock_draft.return_value = {
            "status": "success",
            "data": copy.deepcopy(BASE_VALID_DRAFT),
        }

        # Verifier always returns REVISE
        mock_verify.return_value = {
            "verdict": "REVISE",
            "issues": [
                {
                    "check": "check_required_statements",
                    "detail": "Missing product mismatch note.",
                    "how_to_fix": "Add note on 242/300 product mismatch.",
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
