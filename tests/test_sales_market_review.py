from __future__ import annotations

import unittest
import os
from pathlib import Path
import tempfile

from market_research_agent import (
    MarketResearchAgent,
    MarketResearchContext,
    build_sales_market_review,
)
from tests.test_apparel_costing import costing_input


class SalesMarketReviewAdapterTests(unittest.TestCase):
    def test_review_reads_explicit_musinsa_and_public_fabric_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            musinsa_path = Path(directory) / "musinsa.csv"
            musinsa_path.write_text(
                "project_category,gender,product_type,product_name,brand,product_id,source_url,regular_price,current_price,currency,collected_at,permission_status\n"
                "Outerwear,women,Jacket,Coat,Brand,1,https://example.test/item,120000,90000,KRW,2026-10-01T00:00:00Z,authorized\n",
                encoding="utf-8",
            )
            fabric_path = Path(directory) / "fabric.csv"
            fabric_path.write_text(
                "material,composition,construction,weight_gsm,width,color,source_grade,price,currency,unit,source_url,collected_at,permission_status\n"
                "wool blend,70% wool,plain,280,150cm,navy,public_sale,9000,KRW,yd,https://example.test/cloth,2026-10-01T00:00:00Z,authorized\n",
                encoding="utf-8",
            )
            old_musinsa = os.environ.get("MUSINSA_PRICE_SNAPSHOT_PATH")
            old_fabric = os.environ.get("FABRIC_PRICE_SNAPSHOT_PATH")
            os.environ["MUSINSA_PRICE_SNAPSHOT_PATH"] = str(musinsa_path)
            os.environ["FABRIC_PRICE_SNAPSHOT_PATH"] = str(fabric_path)
            try:
                result = MarketResearchAgent().run(
                    MarketResearchContext(request_id="review-snapshots", planning_request={})
                )
                review = build_sales_market_review(
                    result,
                    planning_draft={"draft_id": "draft-snapshots", "product": {
                        "category": "Outerwear", "product_type": "Jacket", "gender": "women",
                        "material": "wool blend", "proposed_price": 100000, "currency": "KRW",
                    }},
                ).to_dict()
                self.assertEqual(review["category_price_comparison"][0]["current_price_stats"]["median"], 90000)
                self.assertEqual(review["material_cost_analysis"][0]["source_grade"], "public_sale")
                self.assertEqual(review["status"], "partial")
                self.assertEqual({item["field"] for item in review["missing_data"]}, {"customer_segments"})
            finally:
                for key, value in (("MUSINSA_PRICE_SNAPSHOT_PATH", old_musinsa),
                                   ("FABRIC_PRICE_SNAPSHOT_PATH", old_fabric)):
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

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
        self.assertEqual(missing_fields, {"musinsa_prices", "fabric_costs", "customer_segments"})
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
