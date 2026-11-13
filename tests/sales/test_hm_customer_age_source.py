from __future__ import annotations

import os
import unittest

from market_research_agent.sources.sales.hm_kaggle.hm_customer_age_source import (
    CustomerAgeSourceError,
    HmCustomerAgeResearchSource,
    _build_profiles,
)


class HmCustomerAgeResearchTests(unittest.TestCase):
    def test_age_profile_is_aggregate_only_and_maps_leading_bands(self):
        rows = [
            ("Outerwear", "Ladieswear", "20_29", 120, "2018-09-20", "2020-09-22"),
            ("Outerwear", "Ladieswear", "30_39", 80, "2018-09-20", "2020-09-22"),
            ("Outerwear", "Ladieswear", "unknown_age", 5, "2018-09-20", "2020-09-22"),
            ("Outerwear", "Menswear", "20_29", 999, "2018-09-20", "2020-09-22"),
        ]
        result = _build_profiles(
            rows, requested_category="outerwear", gender="women",
            mapping_id="map", mapping_version="v1",
            source_refs=({"object_key": "customers.csv", "version": "v1"},),
        )
        self.assertIsNone(result["unique_customer_denominator"])
        self.assertTrue(result["sample_size_suppressed"])
        self.assertIsNone(result["age_bands"][1]["share"])
        self.assertTrue(all(row["suppressed"] for row in result["age_bands"]))
        self.assertIsNone(result["target_age_cohort"]["share_of_customers_with_known_age"])
        self.assertTrue(result["target_age_cohort"]["suppressed"])
        self.assertEqual(result["age_bands"][-1]["unique_customer_count"], None)
        self.assertTrue(result["age_bands"][-1]["suppressed"])
        self.assertEqual(result["permission_status"], "non_commercial_research_only")
        self.assertFalse(result["operational_eligible"])
        self.assertNotIn("individual_customer_ids", result)

    def test_noncommercial_gate_must_be_explicit(self):
        previous = os.environ.pop("VOGUE_HM_RESEARCH_NONCOMMERCIAL", None)
        try:
            with self.assertRaises(CustomerAgeSourceError):
                HmCustomerAgeResearchSource().profile("Outerwear", gender="women")
        finally:
            if previous is not None:
                os.environ["VOGUE_HM_RESEARCH_NONCOMMERCIAL"] = previous


if __name__ == "__main__":
    unittest.main()
