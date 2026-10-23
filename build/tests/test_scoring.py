import unittest

from scoring import cagr, compute_metrics, peg, quant_score, scenarios


def observation(value, year):
    return {"value": value, "year": year}


class ScoringTests(unittest.TestCase):
    def test_cagr(self):
        self.assertAlmostEqual(cagr(100, 133.1), 0.10)
        self.assertIsNone(cagr(0, 100))
        self.assertIsNone(cagr(-10, 20))
        self.assertIsNone(cagr(None, 20))
        self.assertIsNone(cagr(10, -20))

    def test_peg_uses_percent_not_fraction(self):
        self.assertAlmostEqual(peg(30, 0.20), 1.5)
        self.assertIsNone(peg(30, 0))
        self.assertIsNone(peg(-5, 0.20))

    def test_scenarios(self):
        result = scenarios(100, 4, 0.20, 25)
        self.assertAlmostEqual(result["bear"], 4 * 1.10 ** 5 * 15)
        self.assertAlmostEqual(result["base"], 4 * 1.20 ** 5 * 25)
        self.assertAlmostEqual(result["bull"], 4 * 1.20 ** 5 * 25)
        expected = (result["bull"] / 100 - 1) / abs(result["bear"] / 100 - 1)
        self.assertAlmostEqual(result["asymmetry_ratio"], expected)

    def test_growth_and_multiple_caps(self):
        result = scenarios(1000, 10, 0.50, 50)
        self.assertEqual(result["paths"]["bear"]["eps_growth"], 0.25)
        self.assertEqual(result["paths"]["base"]["eps_growth"], 0.20)
        self.assertEqual(result["paths"]["bull"]["eps_growth"], 0.35)
        self.assertEqual(result["paths"]["bull"]["exit_pe"], 35)
        declining = scenarios(100, 4, -0.10, 25)
        self.assertEqual(declining["paths"]["bear"]["eps_growth"], 0)
        self.assertEqual(declining["paths"]["base"]["eps_growth"], -0.10)

    def test_missing_and_no_bear_loss(self):
        self.assertIsNone(scenarios(None, 4, 0.20, 25)["asymmetry_ratio"])
        self.assertIsNone(scenarios(10, 4, 0.20, 25)["asymmetry_ratio"])

    def test_metrics_align_years(self):
        series = {
            "revenue": [(2020, 100), (2023, 133.1)],
            "eps": [(2020, 2), (2023, 2.662)],
            "operating_income": [(2020, 10), (2023, 26.62)],
            "operating_cash_flow": [(2023, 30)],
            "capex": [(2023, 5)],
            "debt": [(2023, 20)],
            "equity": [(2023, 40)],
        }
        record = {
            "fields": {
                key: {"value": [observation(value, year) for year, value in values]}
                for key, values in series.items()
            }
        }
        record["fields"]["trailing_pe"] = {"value": 20}
        metrics = compute_metrics(record)
        self.assertAlmostEqual(metrics["revenue_cagr"], 0.1)
        self.assertAlmostEqual(metrics["operating_margin_trend"], 0.1)
        self.assertAlmostEqual(metrics["fcf_margin"], 25 / 133.1)
        self.assertAlmostEqual(metrics["debt_equity"], 0.5)
        self.assertAlmostEqual(metrics["peg"], 2)

    def test_missing_lowers_score_and_confidence(self):
        empty = quant_score({})
        self.assertEqual(empty["quant_score"], 0)
        self.assertEqual(empty["confidence"], 0)
        complete = {
            "revenue_cagr": 0.2, "eps_cagr": 0.2,
            "operating_margin": 0.3, "fcf_margin": 0.25,
            "debt_equity": 0, "peg": 0,
        }
        self.assertEqual(quant_score(complete)["quant_score"], 100)
        self.assertEqual(quant_score(complete)["confidence"], 1)


if __name__ == "__main__":
    unittest.main()
