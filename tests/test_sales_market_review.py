from __future__ import annotations

import unittest

from market_research_agent import build_sales_market_review


class SalesMarketReviewAdapterTests(unittest.TestCase):
    def test_target_hypothesis_is_returned_with_age_evidence(self):
        profile = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "permission_status": "non_commercial_research_only",
            "operational_eligible": False,
            "target_age_cohort": {"age_band": "20_39", "suppressed": False,
                                  "share_of_customers_with_known_age": 0.59},
            "age_bands": [{"age_band": "20_29", "unique_customer_count": 100},
                          {"age_band": "30_39", "unique_customer_count": 80}],
        }
        proposal = {"primary_segment": {"description": "겨울 레이어링을 선호하는 고객 가설"}}
        review = build_sales_market_review(
            "review-target-profile",
            product_context={"category": "Outerwear", "gender": "women"},
            customer_age_profile=profile,
            target_audience_proposal=proposal,
        ).to_dict()
        target = review["target_audience_analysis"]
        self.assertEqual(target["status"], "ok")
        self.assertEqual(target["proposal"], proposal)
        self.assertEqual(target["age_cohort"], profile["target_age_cohort"])
        self.assertEqual(review["customer_segment_analysis"][0]["permission_status"], "non_commercial_research_only")
        self.assertFalse(review["customer_segment_analysis"][0]["operational_eligible"])

    def test_review_includes_historical_hm_prices_and_public_fabric_snapshot(self):
        historical_prices = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only",
            "operational_eligible": False,
            "sample_count": 12,
            "price_statistics": {"median": 0.0254, "currency": None},
            "period": {"start": "2020-01-01", "end": "2020-12-31"},
            "sources": [{"object_key": "transactions.parquet", "version": "synthetic-v1"}],
        }
        fabric_prices = ({
            "source_type": "owner_provided_public_fabric_listing_snapshot",
            "source_grade": "public_sale",
            "listed_price_statistics": {"median": 9000, "currency": "KRW"},
            "price_per_meter_krw_statistics": None,
            "estimated_price_per_meter_available": True,
            "verified_price_per_meter_available": False,
            "cost_per_meter_operational_eligible": False,
            "source_object": {"object_key": "fabric.csv", "version": "synthetic-fabric-v1"},
            "permission_status": "owner_provided_snapshot; source reuse terms not independently verified",
        },)
        review = build_sales_market_review(
            "review-snapshots",
            product_context={
                "category": "Outerwear", "product_type": "Jacket", "gender": "women",
                "material": "linen",
            },
            historical_price_analysis=historical_prices,
            fabric_price_analysis=fabric_prices,
        ).to_dict()
        self.assertEqual(review["category_price_comparison"][0]["price_statistics"]["median"], 0.0254)
        self.assertIsNone(review["category_price_comparison"][0]["price_statistics"]["currency"])
        self.assertEqual(review["material_cost_analysis"][0]["source_grade"], "public_sale")
        self.assertTrue(review["material_cost_analysis"][0]["estimated_price_per_meter_available"])
        self.assertFalse(review["material_cost_analysis"][0]["verified_price_per_meter_available"])
        self.assertEqual(review["status"], "partial")
        self.assertEqual({item["field"] for item in review["missing_data"]}, {"customer_segments"})
        self.assertEqual(len(review["evidence"]), 3)  # price, fabric, and benchmark sources
        evidence_by_id = {item["evidence_id"]: item for item in review["evidence"]}
        price_ids = review["category_price_comparison"][0]["evidence_ids"]
        fabric_ids = review["material_cost_analysis"][0]["evidence_ids"]
        self.assertEqual(evidence_by_id[price_ids[0]]["locator"], "transactions.parquet")
        self.assertEqual(evidence_by_id[fabric_ids[0]]["locator"], "fabric.csv")
        self.assertEqual(evidence_by_id[price_ids[0]]["analysis"]["observed"]["sample_count"], 12)
        self.assertIn("result.category_price_comparison", evidence_by_id[price_ids[0]]["supports_fields"])

    def test_internal_review_marks_unconnected_sources_and_does_not_echo_draft(self):
        review = build_sales_market_review(
            "review-001",
            product_context={
                "category": "Outerwear",
                "target_customer": "sensitive free text must not be copied",
                "proposed_price": 42_000,
            },
        ).to_dict()

        self.assertEqual(review["request_id"], "review-001")
        self.assertIsNone(review["draft_id"])
        self.assertEqual(review["status"], "no_results")
        self.assertEqual(review["category_price_comparison"], [])
        self.assertEqual(review["material_cost_analysis"], [])
        self.assertEqual(review["customer_segment_analysis"], [])
        missing_fields = {entry["field"] for entry in review["missing_data"]}
        self.assertEqual(missing_fields, {"historical_hm_transaction_prices", "fabric_costs", "customer_segments"})
        self.assertEqual(review["margin_estimate"]["status"], "benchmark_only")
        self.assertNotIn("target_customer", str(review))
        self.assertNotIn("42_000", str(review))

    def test_source_error_keeps_other_market_results_and_evidence(self):
        prices = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only",
            "sample_count": 24,
            "price_statistics": {"mean": 0.02, "currency": None},
            "sources": [{"object_key": "transactions.parquet", "version": "v1"}],
        }
        fabric = ({
            "source_type": "owner_provided_public_fabric_listing_snapshot",
            "source_object": {"object_key": "fabric.csv", "version": "v2"},
            "permission_status": "owner_provided_snapshot",
            "sources": [],
        },)
        review = build_sales_market_review(
            "review-age-error",
            product_context={"category": "Outerwear", "material": "wool"},
            historical_price_analysis=prices,
            customer_age_error="synthetic DuckDB timeout",
            fabric_price_analysis=fabric,
        ).to_dict()
        self.assertEqual(review["status"], "partial")
        self.assertEqual(review["category_price_comparison"][0]["sample_count"], 24)
        self.assertEqual(review["material_cost_analysis"][0]["source_object"], fabric[0]["source_object"])
        self.assertTrue(review["material_cost_analysis"][0]["evidence_ids"])
        self.assertEqual({item["source_name"] for item in review["evidence"]}, {
            "H&M historical transaction price aggregate",
            "Owner-provided public fabric listing snapshot",
            "NYU Stern Damodaran Margins by Sector (US), January 2026",
        })
        self.assertIn("CUSTOMER_AGE_RESEARCH_FAILED", {item["code"] for item in review["errors"]})

    def test_no_match_and_age_source_failure_are_reported_separately_without_cohort_values(self):
        review = build_sales_market_review(
            "review-source-diagnostics",
            product_context={"category": "니트웨어", "material": "니트", "season": "2027SS"},
            historical_price_source_status="no_match",
            customer_age_source_status="error",
            customer_age_error="H&M research aggregates could not be read safely from R2 (stage=object_metadata; code=ObjectNotFound).",
            fabric_price_source_status="no_match",
        ).to_dict()
        missing = {item["field"]: item for item in review["missing_data"]}
        self.assertEqual(missing["historical_hm_transaction_prices"]["reason"], "no_match")
        self.assertEqual(missing["fabric_costs"]["reason"], "no_match")
        self.assertEqual(missing["customer_segments"]["reason"], "source_error")
        self.assertEqual(review["customer_segment_analysis"], [])
        self.assertIsNone(review["target_audience_analysis"]["age_cohort"])
        self.assertEqual(review["status"], "no_results")
        self.assertIn("stage=object_metadata", review["errors"][0]["message"])

    def test_disabled_research_is_distinct_from_enabled_source_with_no_match(self):
        disabled = build_sales_market_review(
            "review-source-disabled", product_context={"category": "니트웨어"},
            historical_price_source_status="disabled",
            customer_age_source_status="disabled",
        ).to_dict()
        no_match = build_sales_market_review(
            "review-source-disabled", product_context={"category": "니트웨어"},
            historical_price_source_status="no_match",
            customer_age_source_status="no_match",
        ).to_dict()
        disabled_missing = {item["field"]: item for item in disabled["missing_data"]}
        no_match_missing = {item["field"]: item for item in no_match["missing_data"]}
        self.assertEqual(disabled_missing["historical_hm_transaction_prices"]["reason"], "source_not_configured_or_disabled")
        self.assertEqual(no_match_missing["historical_hm_transaction_prices"]["reason"], "no_match")
        self.assertEqual(no_match_missing["customer_segments"]["reason"], "no_match")
        self.assertEqual(no_match["customer_segment_analysis"], [])

    def test_all_successful_research_results_are_visible_in_contract_fields(self):
        profile = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_customer_article_transaction_aggregate",
            "permission_status": "non_commercial_research_only",
            "operational_eligible": False,
            "sources": [{"object_key": "customers.csv", "version": "v1"}],
            "period": {"start": "2020-01-01", "end": "2020-12-31"},
        }
        prices = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only", "sample_count": 24,
            "price_statistics": {"mean": 0.02},
            "sources": [{"object_key": "transactions.parquet", "version": "v1"}],
        }
        fabric = ({
            "source_type": "owner_provided_public_fabric_listing_snapshot",
            "source_object": {"object_key": "fabric.csv", "version": "v2"},
            "sources": [],
        },)
        review = build_sales_market_review(
            "review-all-success", product_context={"category": "Outerwear", "material": "wool"},
            historical_price_analysis=prices, customer_age_profile=profile,
            fabric_price_analysis=fabric,
        ).to_dict()
        self.assertEqual(review["status"], "ok")
        self.assertEqual(len(review["category_price_comparison"]), 1)
        self.assertEqual(len(review["material_cost_analysis"]), 1)
        self.assertEqual(len(review["customer_segment_analysis"]), 1)
        self.assertEqual(len(review["evidence"]), 4)
        evidence_by_id = {item["evidence_id"]: item for item in review["evidence"]}
        self.assertTrue(all(item["analysis"].get("observed") for item in review["evidence"]))
        for rows, output_path in (
            (review["category_price_comparison"], "result.category_price_comparison"),
            (review["material_cost_analysis"], "result.material_cost_analysis"),
            (review["customer_segment_analysis"], "result.customer_segment_analysis"),
        ):
            for row in rows:
                self.assertTrue(row["evidence_ids"])
                for evidence_id in row["evidence_ids"]:
                    self.assertIn(output_path, evidence_by_id[evidence_id]["supports_fields"])

    def test_rejects_non_object_product_context(self):
        with self.assertRaises(TypeError):
            build_sales_market_review("review-002", product_context=[])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
