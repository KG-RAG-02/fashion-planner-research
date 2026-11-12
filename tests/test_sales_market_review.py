from __future__ import annotations

import unittest

from market_research_agent import (
    MarketResearchAgent,
    MarketResearchContext,
    build_sales_market_review,
)


class SalesMarketReviewAdapterTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
