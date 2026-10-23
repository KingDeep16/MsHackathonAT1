import unittest

from skew_core import ai, allocation, metrics, sources


class MetricsTest(unittest.TestCase):
    def test_cagr(self):
        series = [("2021", 100), ("2022", 110), ("2023", 121), ("2024", 133.1)]
        self.assertAlmostEqual(metrics.cagr(series), 0.10, places=6)

    def test_cagr_needs_positive_endpoints(self):
        self.assertIsNone(metrics.cagr([("a", -1), ("b", 1), ("c", 2), ("d", 3)]))
        self.assertIsNone(metrics.cagr([("a", 1), ("b", 2)]))

    def test_scenarios_bear_is_a_real_downside_for_fast_growers(self):
        m = {"price": 100.0, "eps": 4.0, "pe": 25.0, "eps_cagr_3y": 0.40, "revenue_cagr_3y": 0.2}
        s = metrics.scenarios(m)
        # Bear growth is capped at 8%/yr and the multiple compresses to 15x
        self.assertAlmostEqual(s["cases"]["bear"]["growth"], 0.08)
        self.assertLess(s["downside"], 0)
        self.assertGreater(s["upside"], 0)
        self.assertAlmostEqual(s["asymmetry"], round(min(10.0, s["upside"] / -s["downside"]), 2))

    def test_scenarios_fall_back_to_revenue_growth(self):
        m = {"price": 100.0, "eps": 4.0, "pe": 25.0, "eps_cagr_3y": None, "revenue_cagr_3y": 0.12}
        self.assertEqual(metrics.scenarios(m)["growth_basis"], "revenue")

    def test_no_scenarios_without_positive_eps(self):
        self.assertIsNone(metrics.scenarios({"price": 10, "eps": -1, "pe": None, "eps_cagr_3y": None, "revenue_cagr_3y": 0.1}))


class SplitAdjustTest(unittest.TestCase):
    def test_historical_split_restates_earlier_eps(self):
        eps = [("2021", 10.0), ("2022", 12.0), ("2023", 1.4)]
        shares = [("2021", 100), ("2022", 100), ("2023", 1000)]
        adjusted = sources.split_adjust_eps(eps, shares)
        self.assertAlmostEqual(adjusted[0][1], 1.0)
        self.assertAlmostEqual(adjusted[2][1], 1.4)

    def test_split_after_latest_report(self):
        adjusted = sources.split_adjust_eps([("2024", 50.0)], [("2024", 100)], shares_outstanding_now=2500)
        self.assertAlmostEqual(adjusted[0][1], 2.0)

    def test_buybacks_are_not_splits(self):
        eps = [("2021", 10.0), ("2022", 11.0)]
        self.assertEqual(sources.split_adjust_eps(eps, [("2021", 100), ("2022", 95)]), eps)


class AllocationTest(unittest.TestCase):
    def candidates(self):
        return [
            {"ticker": "A", "name": "A", "sector": "Tech", "price": 10, "quant_score": 90, "asymmetry": 4},
            {"ticker": "B", "name": "B", "sector": "Tech", "price": 20, "quant_score": 85, "asymmetry": 3},
            {"ticker": "C", "name": "C", "sector": "Tech", "price": 30, "quant_score": 80, "asymmetry": 2},
            {"ticker": "D", "name": "D", "sector": "Health", "price": 40, "quant_score": 70, "asymmetry": 2},
            {"ticker": "E", "name": "E", "sector": "Energy", "price": 50, "quant_score": 95, "asymmetry": 1.0},
        ]

    def profile(self, **overrides):
        p = {"amount": 100000, "core_pct": 0.3, "etf": "VOO", "num_stocks": 4,
             "max_stock_pct": 0.2, "max_sector_pct": 0.35, "min_asymmetry": 1.5}
        p.update(overrides)
        return p

    def test_respects_caps_and_sums_to_amount(self):
        result = allocation.allocate(self.candidates(), self.profile(), etf_price=500)
        stocks = [h for h in result["holdings"] if h["kind"] == "stock"]
        self.assertTrue(all(h["amount"] <= 20000 + 1e-6 for h in stocks))
        tech = sum(h["amount"] for h in stocks if h["sector"] == "Tech")
        self.assertLessEqual(tech, 35000 + 1e-6)
        self.assertAlmostEqual(result["invested"] + result["cash"], 100000, places=2)
        self.assertLess(result["cash"], 500 + 50 * 5)  # whole-share rounding only

    def test_low_asymmetry_is_excluded_with_reason(self):
        result = allocation.allocate(self.candidates(), self.profile(), etf_price=500)
        excluded = {e["ticker"]: e["reason"] for e in result["excluded"]}
        self.assertIn("E", excluded)
        self.assertIn("below", excluded["E"])

    def test_cap_weights_returns_unplaceable_money(self):
        dollars, unplaced = allocation.cap_weights([1, 1], ["X", "X"], 100, stock_cap=60, sector_cap=50)
        self.assertAlmostEqual(sum(dollars), 50)
        self.assertAlmostEqual(unplaced, 50)


class VerifyTest(unittest.TestCase):
    def test_numbers_must_come_from_facts(self):
        facts = ["MSFT: $12,000.00, 12.0% of the portfolio, 23 shares", "Asymmetry 7.6x"]
        self.assertTrue(ai.verify_numbers("We put $12,000 (12%) into MSFT with 7.6x asymmetry.", facts)["ok"])
        check = ai.verify_numbers("MSFT could rise 40% next year.", facts)
        self.assertFalse(check["ok"])
        self.assertIn("40", check["unverified"])


if __name__ == "__main__":
    unittest.main()
