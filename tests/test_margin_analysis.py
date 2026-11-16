from __future__ import annotations

import unittest

from market_research_agent.domain.apparel_costing import (
    MarginAnalysisInputError,
    calculate_benchmark_margin_estimate,
    calculate_price_range_margin_analysis,
)


class MarginAnalysisTests(unittest.TestCase):
    def test_industry_benchmark_is_not_presented_as_product_costing(self):
        result = calculate_benchmark_margin_estimate(
            selling_price=129000,
            currency="KRW",
            fabric_unit_cost=6666.67,
            fabric_unit="KRW/m",
        )

        self.assertEqual(result["status"], "benchmark_estimate")
        self.assertEqual(result["estimated_gross_margin_rate"], 0.5688)
        self.assertEqual(result["implied_total_cost_allowance"], 55624.8)
        self.assertEqual(result["fabric_unit"], "KRW/m")
        self.assertNotIn("fabric_cost_per_garment", result)
        self.assertIn("not a product-specific margin observation", result["limitations"][-1])

    def test_price_range_uses_only_p25_and_p75(self):
        result = calculate_price_range_margin_analysis(
            scaled_price_statistics={"p25": 29.99, "p75": 47.99},
            usd_krw_rate=1334.71,
            fabric_unit_cost=6666.67,
            fabric_unit="KRW/m",
        )

        self.assertEqual(set(result["ranges"]), {"quartile_p25_p75"})
        p25 = result["ranges"]["quartile_p25_p75"]["lower_bound"]
        p75 = result["ranges"]["quartile_p25_p75"]["upper_bound"]
        self.assertEqual(p25["price_krw"], 40027.95)
        self.assertEqual(p25["remaining_non_fabric_cost_allowance_krw_after_one_meter"], 10593.38)
        self.assertEqual(p75["price_krw"], 64052.73)
        self.assertEqual(p75["remaining_non_fabric_cost_allowance_krw_after_one_meter"], 20952.87)

    def test_range_requires_valid_quartile_data(self):
        with self.assertRaises(MarginAnalysisInputError):
            calculate_price_range_margin_analysis(
                scaled_price_statistics={"p25": None, "p75": 47.99},
                usd_krw_rate=1334.71,
                fabric_unit_cost=6666.67,
                fabric_unit="KRW/m",
            )


if __name__ == "__main__":
    unittest.main()
