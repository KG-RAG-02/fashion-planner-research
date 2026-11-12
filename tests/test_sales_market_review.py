from __future__ import annotations

import unittest

from market_research_agent import (
    MarketResearchAgent,
    MarketResearchContext,
    build_sales_market_review,
)
from tests.test_apparel_costing import costing_input


class SalesMarketReviewAdapterTests(unittest.TestCase):
    def test_target_sentence_uses_computed_age_cohort_and_llm_style_label(self):
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="review-target-profile", planning_request={})
        )
        profile = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "permission_status": "non_commercial_research_only",
            "operational_eligible": False,
            "target_age_cohort": {"age_band": "20_39", "suppressed": False,
                                  "share_of_customers_with_known_age": 0.59},
            "age_bands": [{"age_band": "20_29", "unique_customer_count": 100},
                          {"age_band": "30_39", "unique_customer_count": 80}],
        }
        style = {"style_label": "datewear", "visual_features": ["파스텔 톤"],
                 "style_rationale": "밝은 색감과 부드러운 실루엣입니다."}
        review = build_sales_market_review(
            result,
            planning_draft={"draft_id": "target-001", "product": {"category": "Outerwear", "gender": "women"}},
            customer_age_profile=profile,
            image_style_analysis=style,
        ).to_dict()
        target = review["target_audience_analysis"]["target_sentence"]
        self.assertIn("20~30대", target)
        self.assertIn("여성용 데이트룩", target)
        self.assertIn("비상업적 연구 가설", target)
        self.assertEqual(review["customer_segment_analysis"][0]["permission_status"], "non_commercial_research_only")
        self.assertFalse(review["customer_segment_analysis"][0]["operational_eligible"])

    def test_review_includes_historical_hm_prices_and_public_fabric_snapshot(self):
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="review-snapshots", planning_request={})
        )
        historical_prices = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only",
            "operational_eligible": False,
            "sample_count": 12,
            "price_statistics": {"median": 0.0254, "currency": None},
        }
        fabric_prices = ({
            "source_type": "owner_provided_public_fabric_listing_snapshot",
            "source_grade": "public_sale",
            "listed_price_statistics": {"median": 9000, "currency": "KRW"},
            "price_per_meter_krw_statistics": None,
            "estimated_price_per_meter_available": True,
            "verified_price_per_meter_available": False,
            "cost_per_meter_operational_eligible": False,
        },)
        review = build_sales_market_review(
            result,
            planning_draft={"draft_id": "draft-snapshots", "product": {
                "category": "Outerwear", "product_type": "Jacket", "gender": "women",
                "material": "linen", "proposed_price": 100000, "currency": "KRW",
            }},
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

    def test_internal_review_marks_unconnected_sources_and_does_not_echo_draft(self):
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="review-001", planning_request={})
        )

        review = build_sales_market_review(
            result,
            planning_draft={
                "draft_id": "draft-001",
                "target_customer": "sensitive free text must not be copied",
                "proposed_price": 42_000,
            },
        ).to_dict()

        self.assertEqual(review["request_id"], "review-001")
        self.assertEqual(review["draft_id"], "draft-001")
        self.assertEqual(review["status"], "no_results")
        self.assertEqual(review["category_price_comparison"], [])
        self.assertEqual(review["material_cost_analysis"], [])
        self.assertEqual(review["customer_segment_analysis"], [])
        missing_fields = {entry["field"] for entry in review["missing_data"]}
        self.assertEqual(missing_fields, {"historical_hm_transaction_prices", "fabric_costs", "customer_segments"})
        self.assertEqual(review["margin_estimate"]["status"], "no_results")
        self.assertNotIn("target_customer", str(review))
        self.assertNotIn("42_000", str(review))

    def test_rejects_non_object_draft(self):
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="review-002", planning_request={})
        )
        with self.assertRaises(TypeError):
            build_sales_market_review(result, planning_draft=[])  # type: ignore[arg-type]

    def test_complete_cost_sheet_adds_a_partial_review_without_filling_other_sources(self):
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="review-003", planning_request={})
        )
        review = build_sales_market_review(
            result,
            planning_draft={
                "draft_id": "draft-003",
                "product": {"category": "Outerwear", "proposed_price": 10000, "currency": "KRW"},
                "costing": costing_input(),
            },
        ).to_dict()
        self.assertEqual(review["status"], "partial")
        self.assertEqual(review["margin_estimate"]["status"], "ok")
        self.assertEqual(review["margin_estimate"]["calculation"]["landed_cost"], 5009)
        self.assertEqual(review["category_price_comparison"], [])
        self.assertEqual(review["material_cost_analysis"][0]["source_type"], "planning_draft_costing_input")
        self.assertEqual(review["material_cost_analysis"][0]["source_verification"], "caller_supplied_not_independently_verified")


if __name__ == "__main__":
    unittest.main()
