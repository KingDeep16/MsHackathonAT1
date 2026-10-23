import unittest

from ai_analyst import validate_analysis, verify_numbers
from data_sources import extract_annual


class DataTests(unittest.TestCase):
    def test_comparative_periods_use_end_year_and_latest_restatement(self):
        observations = [
            {
                "form": "10-K", "fp": "FY", "fy": 2024,
                "start": "2022-01-01", "end": "2022-12-31",
                "filed": "2025-02-01", "val": 120, "accn": "0001-25-001",
            },
            {
                "form": "10-K", "fp": "FY", "fy": 2022,
                "start": "2022-01-01", "end": "2022-12-31",
                "filed": "2023-02-01", "val": 100, "accn": "0001-23-001",
            },
            {
                "form": "10-K", "fp": "FY", "fy": 2024,
                "start": "2024-10-01", "end": "2024-12-31",
                "filed": "2025-02-01", "val": 40, "accn": "0001-25-001",
            },
        ]
        data = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": observations}}}}}
        result = extract_annual(data, 1)["revenue"]["value"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["year"], 2022)
        self.assertEqual(result[0]["value"], 120)
        self.assertIn("Archives", result[0]["source"])

    def test_foreign_filer_missing_is_empty(self):
        result = extract_annual({"facts": {"ifrs-full": {}}}, 1)
        self.assertTrue(all(node["value"] == [] for node in result.values()))


class AITests(unittest.TestCase):
    def test_memo_rounding(self):
        facts = {"invested_usd": 99876.54, "weight_pct": 12.346}
        checked = verify_numbers("Invest $99,877, with a 12.35% position.", facts)
        self.assertTrue(checked["verified"])
        self.assertEqual(checked["checked_count"], 2)
        self.assertFalse(verify_numbers("Expect 99% returns.", facts)["verified"])

    def test_ids_do_not_count_as_figures(self):
        result = verify_numbers("See F3 and E1.", {})
        self.assertEqual(result["checked_count"], 0)

    def test_unknown_citation_rejected(self):
        bullet = {"text": "Evidence unavailable.", "citations": ["E1"]}
        payload = {
            "earnings_takeaways": [bullet] * 3,
            "bull_points": [bullet] * 2,
            "bear_points": [bullet] * 2,
            "thesis_breakers": [bullet],
            "sentiment": {"score": 0, "reason": bullet},
            "moat": {"rating": "none", "reason": bullet},
            "conviction_adjustment": {"value": 0, "reason": bullet},
        }
        self.assertEqual(validate_analysis(payload, {"E1": {}})["status"], "available")
        with self.assertRaises(ValueError):
            validate_analysis(payload, {})


if __name__ == "__main__":
    unittest.main()
