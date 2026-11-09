from __future__ import annotations

import unittest

from market_research_agent.domain.sales import MarketObservation, SalesObservation, compare_sales_to_market


class ComparabilityTests(unittest.TestCase):
    def _sales(self, **changes):
        values = dict(
            observation_id="sales-1", metric_name="purchase_rows", value=20,
            unit="purchase_rows", evidence_ids=("sales-evidence",),
            category="Outerwear", period_start="2018-01-01", period_end="2018-01-01",
            period_grain="week", geography="KR", channel="online",
            metric_definition="Historical transaction row count",
            product_mapping="map-v1",
        )
        values.update(changes)
        return SalesObservation(**values)

    def _market(self, **changes):
        values = dict(
            observation_id="market-1", metric_name="purchase_rows", measure_type="transaction_actual",
            value=100, unit="purchase_rows", evidence_ids=("market-evidence",),
            category="Outerwear", period_start="2018-01-01", period_end="2018-01-01",
            period_grain="week", geography="KR", channel="online",
            metric_definition="Historical transaction row count",
            product_mapping_version="map-v1",
        )
        values.update(changes)
        return MarketObservation(**values)

    def test_historical_purchase_rows_never_pass_market_comparison_gate(self):
        result = compare_sales_to_market(
            self._sales(), self._market(),
            sales_permission_authorized=True, market_permission_authorized=True,
        )
        self.assertFalse(result.comparable)
        self.assertIn("sales_measure:historical_purchase_rows_not_market_size", result.mismatches)

    def test_unknown_scope_and_permission_fail_closed(self):
        result = compare_sales_to_market(
            self._sales(channel=None), self._market(geography=None),
            sales_permission_authorized=True, market_permission_authorized=False,
        )
        self.assertFalse(result.comparable)
        self.assertIn("channel:unknown", result.mismatches)
        self.assertIn("geography:unknown", result.mismatches)
        self.assertIn("permission:not_authorized", result.mismatches)

    def test_dimension_mismatch_blocks_comparison(self):
        result = compare_sales_to_market(
            self._sales(), self._market(category="Footwear"),
            sales_permission_authorized=True, market_permission_authorized=True,
        )
        self.assertFalse(result.comparable)
        self.assertIn("category:mismatch", result.mismatches)


if __name__ == "__main__":
    unittest.main()
