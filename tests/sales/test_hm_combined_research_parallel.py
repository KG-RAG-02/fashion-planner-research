from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

from market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source import HmCombinedResearchSource
from market_research_agent.sources.sales.hm_kaggle.hm_customer_age_source import CustomerAgeSourceError


class HmCombinedResearchParallelTests(unittest.TestCase):
    def test_disabled_hm_research_is_distinct_from_an_empty_configured_source(self):
        with patch.dict(os.environ, {"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "false"}, clear=True), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.R2FabricPriceSource.analyze", return_value=()):
            result = HmCombinedResearchSource().analyze({"category": "니트웨어", "material": "니트"})
        self.assertEqual(result["customer_age_source_status"], "disabled")
        self.assertEqual(result["historical_price_source_status"], "disabled")
        self.assertIsNone(result["customer_age_profile"])
        self.assertIsNone(result["historical_price"])

    def test_configured_hm_sources_report_no_match_without_invented_values(self):
        with patch.dict(os.environ, {"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true"}, clear=True), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.R2FabricPriceSource.analyze", return_value=()), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmCustomerAgeResearchSource.profile", return_value=None), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmHistoricalPriceResearchSource.analyze", return_value=None):
            result = HmCombinedResearchSource().analyze({"category": "니트웨어", "material": "니트"})
        self.assertEqual(result["customer_age_source_status"], "no_match")
        self.assertEqual(result["historical_price_source_status"], "no_match")
        self.assertIsNone(result["customer_age_profile"])
        self.assertIsNone(result["historical_price"])
        self.assertEqual(result["fabric_prices"], ())

    def test_independent_sources_run_concurrently_and_preserve_partial_results(self):
        barrier = threading.Barrier(3)

        def fabric(_product):
            barrier.wait(timeout=2)
            return ({"material": "cotton"},)

        def age(self, category, *, gender=None):
            barrier.wait(timeout=2)
            raise CustomerAgeSourceError("age source error")

        def price(self, category, *, gender=None, product_type=None, season=None):
            barrier.wait(timeout=2)
            return {"category": category, "sample_count": 17}

        with patch.dict(os.environ, {"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true"}), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.R2FabricPriceSource.analyze", side_effect=fabric), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmCustomerAgeResearchSource.profile", age), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmHistoricalPriceResearchSource.analyze", price):
            result = HmCombinedResearchSource().analyze({"category": "Outerwear", "material": "cotton"})
        self.assertEqual(result["fabric_prices"], ({"material": "cotton"},))
        self.assertEqual(result["historical_price"]["sample_count"], 17)
        self.assertEqual(result["customer_age_error"], "age source error")

    def test_aggregate_snapshot_path_uses_one_combined_hm_read(self):
        def fabric(_product):
            raise AssertionError("current combined snapshot should include fabric data")

        def aggregate(_product):
            return {
                "customer_age_profile": {"category": "Outerwear"},
                "customer_age_error": None,
                "customer_age_source_status": "available",
                "historical_price": {"category": "Outerwear"},
                "historical_price_error": None,
                "historical_price_source_status": "available",
                "fabric_prices": (), "fabric_price_error": None,
                "fabric_price_source_status": "no_match",
            }

        with patch.dict(os.environ, {
            "VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true",
            "VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY": "derived/research-aggregate-snapshots/synthetic.json",
        }), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.R2FabricPriceSource.analyze", side_effect=fabric), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmResearchAggregateSource.analyze", side_effect=aggregate) as hm_read, \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmCustomerAgeResearchSource.profile", side_effect=AssertionError("raw age query used")), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmHistoricalPriceResearchSource.analyze", side_effect=AssertionError("raw price query used")):
            result = HmCombinedResearchSource().analyze({"category": "Outerwear", "material": "cotton"})
        self.assertEqual(hm_read.call_count, 1)
        self.assertEqual(result["customer_age_profile"], {"category": "Outerwear"})
        self.assertEqual(result["historical_price"], {"category": "Outerwear"})
        self.assertIsNone(result["customer_age_error"])
        self.assertIsNone(result["historical_price_error"])

    def test_failed_aggregate_snapshot_does_not_read_other_r2_objects(self):
        with patch.dict(os.environ, {
            "VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true",
            "VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY": "derived/research-aggregate-snapshots/broken.json",
        }), \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.R2FabricPriceSource.analyze", side_effect=AssertionError("standalone R2 read must not be attempted")) as fabric_read, \
             patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmResearchAggregateSource.analyze", side_effect=ValueError("invalid snapshot")):
            result = HmCombinedResearchSource().analyze({"category": "Outerwear", "material": "cotton"})
        self.assertEqual(result["customer_age_source_status"], "error")
        self.assertEqual(result["historical_price_source_status"], "error")
        self.assertEqual(result["fabric_prices"], ())
        self.assertEqual(result["fabric_price_source_status"], "error")
        self.assertIsNotNone(result["fabric_price_error"])
        fabric_read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
