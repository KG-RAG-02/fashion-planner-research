"""Fail-closed comparability checks for sales and market observations."""

from __future__ import annotations

from ...domain.sales import ComparabilityResult, MarketObservation, SalesObservation, compare_sales_to_market


class ObservationComparabilityValidator:
    """Validate whether two records share a sufficiently identical scope.

    The validator only returns a decision record. It never calculates a ratio,
    market share, growth rate, or commercial recommendation.
    """

    def compare(
        self,
        sales: SalesObservation,
        market: MarketObservation,
        *,
        sales_permission_authorized: bool,
        market_permission_authorized: bool,
    ) -> ComparabilityResult:
        return compare_sales_to_market(
            sales,
            market,
            sales_permission_authorized=sales_permission_authorized,
            market_permission_authorized=market_permission_authorized,
        )
