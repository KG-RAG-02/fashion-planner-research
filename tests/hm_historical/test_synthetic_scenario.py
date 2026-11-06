from __future__ import annotations

import unittest

from market_research_agent.sources.hm_historical.synthetic_scenario import (
    SEED,
    analyze_scenario,
    build_scenario,
    validate_scenario,
)


class SyntheticMerchScenarioTests(unittest.TestCase):
    def test_generated_scenario_is_reproducible_and_synthetic_only(self) -> None:
        first = build_scenario(SEED)
        second = build_scenario(SEED)
        self.assertEqual(first, second)
        self.assertEqual(first["data_status"], "synthetic")
        self.assertTrue(all(record["style_id"].startswith("SYN-STYLE-") for record in first["styles"]))
        self.assertFalse(validate_scenario(first))

    def test_style_article_sku_hierarchy_and_weekly_data_are_complete(self) -> None:
        scenario = build_scenario()
        self.assertEqual(len(scenario["styles"]), 10)
        self.assertEqual(len(scenario["articles"]), 20)
        self.assertEqual(len(scenario["skus"]), 60)
        self.assertEqual(len(scenario["sales"]), 60 * 12)
        self.assertEqual(len(scenario["inventory"]), 60 * 12)
        self.assertTrue(all(row["source_id"] == scenario["source_id"] and row["version"] == scenario["version"] and row["as_of"] for row in scenario["sales"]))
        self.assertTrue(all(row["data_status"] == "synthetic" for row in scenario["inventory"]))

    def test_inventory_ledger_conserves_units_each_week(self) -> None:
        for row in build_scenario()["inventory"]:
            self.assertEqual(
                row["opening_units"] + row["receipt_units"] + row["adjustment_units"] - row["units_sold"],
                row["closing_units"],
            )

    def test_analysis_recomputes_sales_revenue_and_descriptive_markdown(self) -> None:
        scenario = build_scenario()
        report = analyze_scenario(scenario)
        self.assertEqual(report["data_status"], "synthetic")
        self.assertEqual(report["totals"]["units_sold"], sum(row["units_sold"] for row in scenario["sales"]))
        self.assertEqual(report["totals"]["synthetic_revenue"], sum(row["units_sold"] * row["selling_price"] for row in scenario["sales"]))
        self.assertFalse(report["markdown_comparison"]["causal_effect_claim"])
        self.assertEqual(sum(row["responses"] for row in report["feedback_theme_counts"]), len(scenario["feedback"]))
        metrics = {row["kpi_id"]: row["value"] for row in report["campaign_metrics"]}
        self.assertEqual(metrics["SYN-KPI-PROMO-CTR"], 5.44)
        campaign = scenario["campaigns"][0]
        self.assertEqual(campaign["items_purchased"], sum(row["units_sold"] for row in scenario["sales"] if row["promotion_id"] == campaign["promotion_id"] and row["channel"] == campaign["channel"]))
        self.assertEqual(campaign["item_revenue"], sum(row["units_sold"] * row["selling_price"] for row in scenario["sales"] if row["promotion_id"] == campaign["promotion_id"] and row["channel"] == campaign["channel"]))

    def test_validation_catches_broken_inventory_balance(self) -> None:
        scenario = build_scenario()
        scenario["inventory"][0]["closing_units"] += 1
        self.assertIn("inventory balance equation failed", validate_scenario(scenario))


if __name__ == "__main__":
    unittest.main()
