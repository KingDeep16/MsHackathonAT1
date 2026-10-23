import unittest
from collections import defaultdict

from allocation import allocate, constrained_targets


def stock(ticker, sector, score=70, price=10, ratio=2):
    return {
        "ticker": ticker, "sector": sector, "quant_score": score, "price": price,
        "scenarios": {"asymmetry_ratio": ratio, "reason": None},
        "metrics": {"eps_cagr": 0.15}, "fetch_errors": [],
    }


class AllocationTests(unittest.TestCase):
    def test_stock_cap_redistributes(self):
        stocks = [
            {"ticker": "A", "sector": "A", "final_score": 100},
            {"ticker": "B", "sector": "B", "final_score": 10},
            {"ticker": "C", "sector": "C", "final_score": 10},
        ]
        targets = constrained_targets(stocks, 600, 250, 1000)
        self.assertAlmostEqual(targets["A"], 250)
        self.assertAlmostEqual(targets["B"], 175)
        self.assertAlmostEqual(targets["C"], 175)

    def test_sector_cap_redistributes(self):
        stocks = [
            {"ticker": "A", "sector": "Tech", "final_score": 100},
            {"ticker": "B", "sector": "Tech", "final_score": 100},
            {"ticker": "C", "sector": "Health", "final_score": 10},
        ]
        targets = constrained_targets(stocks, 700, 1000, 400)
        self.assertAlmostEqual(targets["A"] + targets["B"], 400)
        self.assertAlmostEqual(targets["C"], 300)

    def test_whole_shares_caps_and_conservation(self):
        stocks = [
            stock(f"S{i}", ["Tech", "Health", "Finance", "Industrial"][i % 4],
                  score=100 - i * 5, price=17 + i * 11)
            for i in range(8)
        ]
        result = allocate(stocks, 100_000, 0.30, "VOO", 501.27, 8, 0.15, 0.35)
        sectors = defaultdict(float)
        for holding in result["holdings"]:
            self.assertIsInstance(holding["shares"], int)
            self.assertGreater(holding["shares"], 0)
            self.assertAlmostEqual(holding["amount"], holding["shares"] * holding["price"])
            if holding["kind"] == "stock":
                self.assertLessEqual(holding["amount"], 15_000 + 1e-6)
                sectors[holding["sector"]] += holding["amount"]
        for total in sectors.values():
            self.assertLessEqual(total, 35_000 + 1e-6)
        self.assertAlmostEqual(
            sum(holding["amount"] for holding in result["holdings"]),
            100_000 - result["cash"],
        )
        self.assertGreaterEqual(result["cash"], 0)

    def test_infeasible_capacity_stays_cash(self):
        result = allocate([stock("A", "Tech")], 1000, 0, "VOO", 100, 8, 0.15, 0.35)
        self.assertEqual(result["invested"], 150)
        self.assertEqual(result["cash"], 850)

    def test_low_asymmetry_and_missing_price_excluded(self):
        stocks = [
            stock("LOW", "Tech", ratio=1.49),
            stock("MISS", "Tech", price=None),
            stock("OK", "Health", ratio=1.5),
        ]
        result = allocate(stocks, 1000, 0, "VOO", None, 8, 0.15, 0.35)
        self.assertEqual([holding["ticker"] for holding in result["holdings"]], ["OK"])
        self.assertEqual({row["ticker"] for row in result["screened"]}, {"LOW", "MISS"})

    def test_missing_core_price_keeps_core_cash(self):
        result = allocate(
            [stock("A", "Tech")], 1000, 0.30, "VOO", None, 8, 0.15, 0.35
        )
        self.assertEqual(result["cash"], 850)
        self.assertTrue(any("core budget" in note for note in result["notes"]))


if __name__ == "__main__":
    unittest.main()
