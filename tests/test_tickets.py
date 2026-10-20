"""
Unit tests for deterministic tickets tools in tools/tickets.py.
Run under Python 3.11 venv.
"""
import unittest
from tools.tickets import (
    load_tickets,
    data_quality_report,
    cluster_stats,
    fix_usage_map,
    get_cluster_tickets,
    compute_cluster_signals,
)


class TestTicketTools(unittest.TestCase):

    def test_load_tickets_windows(self):
        baseline = load_tickets(window="baseline")
        replay = load_tickets(window="replay")
        all_tickets = load_tickets(window="all")

        self.assertEqual(len(baseline), 157)
        self.assertEqual(len(replay), 143)
        self.assertEqual(len(all_tickets), 300)

        # Invalid window
        err = load_tickets(window="future")
        self.assertIn("error", err)

    def test_data_quality_report(self):
        report = data_quality_report()
        self.assertEqual(report["total_tickets"], 300)

        # Product in summary vs product column mismatch
        mismatch = report["summary_product_mismatch"]
        self.assertEqual(mismatch["count"], 242)
        self.assertAlmostEqual(mismatch["rate"], 0.807, places=3)
        self.assertEqual(len(mismatch["samples"]), 5)

        # Same-day > 24h
        same_day = report["same_day_resolution_gt_24h"]
        self.assertEqual(same_day["same_day_total"], 44)
        self.assertEqual(same_day["gt_24h_count"], 32)

        # Repetitive
        rep = report["repetitive_summary"]
        self.assertEqual(rep["repetitive_count"], 195)
        self.assertEqual(rep["repetitive_rate"], 0.65)

    def test_cluster_stats(self):
        stats_all = cluster_stats(window="all")
        self.assertEqual(stats_all["total_tickets"], 300)
        self.assertEqual(stats_all["total_clusters"], 24)

        clusters = stats_all["clusters"]
        self.assertEqual(len(clusters), 24)

        cid = "Access Control|Access Control - variant 3"
        self.assertIn(cid, clusters)
        c_info = clusters[cid]

        self.assertEqual(c_info["category"], "Access Control")
        self.assertEqual(c_info["subcategory"], "Access Control - variant 3")
        self.assertGreater(c_info["ticket_count"], 0)
        self.assertIn("P1", c_info["severity_mix"])
        self.assertTrue(len(c_info["distinct_fix_ids"]) > 0)
        self.assertTrue(len(c_info["top_fix_ids"]) > 0)
        self.assertTrue(all(f in c_info["distinct_fix_ids"] for f in c_info["top_fix_ids"]))
        self.assertGreater(c_info["top_fix_share"], 0.0)
        self.assertIn("has_dominant_fix", c_info)
        self.assertIsInstance(c_info["has_dominant_fix"], bool)

        # Baseline window
        stats_base = cluster_stats(window="baseline")
        self.assertEqual(stats_base["total_tickets"], 157)
        self.assertEqual(stats_base["total_clusters"], 24)

        # Invalid window
        err = cluster_stats(window="invalid")
        self.assertIn("error", err)

    def test_fix_usage_map(self):
        fmap = fix_usage_map()
        self.assertEqual(fmap["total_fixes"], 20)
        fixes = fmap["fixes"]
        self.assertEqual(len(fixes), 20)

        for fix_id_str, fix_data in fixes.items():
            self.assertGreater(fix_data["total_occurrences"], 0)
            self.assertGreater(fix_data["category_count"], 0)
            self.assertTrue(len(fix_data["clusters"]) > 0)

    def test_get_cluster_tickets(self):
        cid = "Access Control|Access Control - variant 3"
        res = get_cluster_tickets(cluster_id=cid, window="all", limit=5)

        self.assertNotIn("error", res)
        self.assertEqual(res["cluster_id"], cid)
        self.assertEqual(len(res["tickets"]), 5)
        self.assertIn("fix_id", res["tickets"][0])
        self.assertIn("ticket_id", res["tickets"][0])

        # Unknown cluster_id
        bad_res = get_cluster_tickets(cluster_id="Nonexistent|Cluster")
        self.assertIn("error", bad_res)

    def test_compute_cluster_signals(self):
        cid = "Access Control|Access Control - variant 3"
        signals = compute_cluster_signals(cluster_id=cid)

        self.assertNotIn("error", signals)
        self.assertEqual(signals["cluster_id"], cid)
        self.assertIn("baseline_ticket_count", signals)
        self.assertIn("replay_ticket_count", signals)
        self.assertIn("volume_change", signals)
        self.assertIn("repetitive_rate_change", signals)
        self.assertIn("average_resolution_change", signals)
        self.assertIn("fix_ids_new_in_replay", signals)
        self.assertIn("new_fix_shares_in_replay", signals)
        # Ensure no lifecycle decision is present
        self.assertNotIn("lifecycle_signal", signals)

        # Unknown cluster_id
        bad_signals = compute_cluster_signals(cluster_id="Unknown|Unknown")
        self.assertIn("error", bad_signals)

    def test_score_clusters(self):
        from tools.tickets import score_clusters
        res = score_clusters(window="baseline")

        self.assertNotIn("error", res)
        self.assertEqual(res["total_clusters"], 24)
        clusters = res["clusters"]
        self.assertEqual(len(clusters), 24)

        # Check sorted descending by priority_score
        scores = [c["priority_score"] for c in clusters]
        self.assertEqual(scores, sorted(scores, reverse=True))

        top = clusters[0]
        self.assertIn("breakdown", top)
        bd = top["breakdown"]
        self.assertIn("volume", bd)
        self.assertIn("repetition", bd)
        self.assertIn("fix_inconsistency", bd)
        self.assertIn("severity", bd)
        self.assertTrue(0.0 <= bd["volume"] <= 1.0)
        self.assertTrue(0.0 <= bd["repetition"] <= 1.0)
        self.assertTrue(0.0 <= bd["fix_inconsistency"] <= 1.0)
        self.assertTrue(0.0 <= bd["severity"] <= 1.0)

        # Invalid window
        err = score_clusters(window="invalid")
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main()
