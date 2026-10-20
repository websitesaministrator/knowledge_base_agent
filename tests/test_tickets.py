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
        self.assertIn(c_info["top_fix_id"], c_info["distinct_fix_ids"])
        self.assertGreater(c_info["top_fix_share"], 0.0)

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
        self.assertIn("baseline_volume", signals)
        self.assertIn("replay_volume", signals)
        self.assertIn("volume_change", signals)
        self.assertIn("lifecycle_signal", signals)
        self.assertIn(signals["lifecycle_signal"], ["KEEP", "UPDATE", "RETIRE"])

        # Unknown cluster_id
        bad_signals = compute_cluster_signals(cluster_id="Unknown|Unknown")
        self.assertIn("error", bad_signals)


if __name__ == "__main__":
    unittest.main()
