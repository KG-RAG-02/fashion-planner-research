from __future__ import annotations

import json
import unittest

from market_research_agent.sources.hm_historical.planning_operations import (
    calculate_ratio_kpi,
    get_product,
    load_fixture,
    validate_fixture,
)


class PlanningOperationsTests(unittest.TestCase):
    def test_all_business_collections_declare_synthetic_provenance(self) -> None:
        fixture = load_fixture()
        self.assertEqual(fixture["data_status"], "synthetic")
        for name in ("line_plans", "markdown_reviews", "kpi_definitions", "product_details", "campaigns"):
            for record in fixture[name]:
                self.assertEqual(record["data_status"], "synthetic")
                self.assertTrue(record["source_id"])
                self.assertTrue(record["version"])
                self.assertTrue(record["as_of"])
                self.assertTrue(record["limitations"])

    def test_line_plan_markdown_product_and_campaign_references_are_valid(self) -> None:
        fixture = load_fixture()
        plan = fixture["line_plans"][0]
        self.assertEqual(plan["season_id"], "SYN-27SS")
        self.assertEqual(plan["season_label"], "27SS")
        self.assertEqual(sum(row["target_style_count"] for row in plan["product_groups"]), 20)
        markdown = fixture["markdown_reviews"][0]
        self.assertEqual(markdown["style_id"], "LW-2601")
        self.assertIsNone(markdown["observed_outcome"])
        self.assertEqual(fixture["campaigns"][0]["product_style_ids"], ["LW-2601"])
        self.assertEqual(fixture["campaigns"][0]["metrics"]["item_revenue"], None)

    def test_invalid_markdown_date_or_price_is_rejected(self) -> None:
        fixture = load_fixture()
        fixture["markdown_reviews"][0]["effective_to"] = "2026-01-01"
        with self.assertRaises(ValueError):
            validate_fixture(fixture)

    def test_kpi_is_not_calculable_with_zero_or_missing_denominator(self) -> None:
        zero = calculate_ratio_kpi(4, 0)
        missing = calculate_ratio_kpi(None, None, ["units_sold", "available_units"])
        self.assertEqual(zero["status"], "not_calculable")
        self.assertIsNone(zero["value"])
        self.assertIn("non_zero_denominator", zero["missing_fields"])
        self.assertEqual(missing["status"], "not_calculable")
        self.assertIn("available_units", missing["missing_fields"])
        self.assertEqual(calculate_ratio_kpi(5, 10)["value"], 50.0)

    def test_product_lookup_keeps_synthetic_and_observed_catalog_data_separate(self) -> None:
        synthetic = get_product("LW-2601")
        # The Project_07 repository must work without inheriting Project_06's
        # local Kaggle data directory. Its optional adapter is tested with temp fixtures.
        self.assertEqual(synthetic["data_status"], "synthetic")
        self.assertIsNone(get_product("0108775015"))



if __name__ == "__main__":
    unittest.main()
