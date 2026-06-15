import unittest

from earnings_growth import select_canslim_c_growth


class EarningsGrowthSelectionTests(unittest.TestCase):
    def test_quarterly_yoy_takes_priority_over_annual_growth(self):
        result = select_canslim_c_growth(
            {
                "earningsGrowth": 0.3343434343,
                "earningsQuarterlyGrowth": 4.921,
                "revenueGrowth": 0.692,
            }
        )

        self.assertAlmostEqual(result["eps_growth"], 4.921)
        self.assertEqual(result["eps_src"], "quarterly_eps")
        self.assertEqual(result["eps_basis"], "최근 분기 EPS/순이익 YoY")
        self.assertFalse(result["data_missing"])

    def test_annual_growth_is_only_fallback_when_quarterly_missing(self):
        result = select_canslim_c_growth(
            {
                "earningsGrowth": 0.3343434343,
                "revenueGrowth": 0.692,
            }
        )

        self.assertAlmostEqual(result["eps_growth"], 0.3343434343)
        self.assertEqual(result["eps_src"], "annual_eps")
        self.assertEqual(result["eps_basis"], "연간/TTM EPS 성장률")
        self.assertFalse(result["data_missing"])

    def test_revenue_proxy_is_last_resort(self):
        result = select_canslim_c_growth({"revenueGrowth": 0.30})

        self.assertAlmostEqual(result["eps_growth"], 0.18)
        self.assertEqual(result["eps_src"], "revenue_proxy")
        self.assertEqual(result["eps_basis"], "매출 성장률 프록시")
        self.assertFalse(result["data_missing"])


if __name__ == "__main__":
    unittest.main()
