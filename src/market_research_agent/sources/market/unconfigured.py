"""Adapter contract for separately researched, permission-cleared market data."""

from __future__ import annotations

from ...domain.models import MarketResearchContext, ResultStatus, SourceReport


class UnconfiguredMarketDataSource:
    """Explicit missing-source result; never invent market measurements."""

    def research(self, context: MarketResearchContext) -> SourceReport:
        return SourceReport(
            status=ResultStatus.NO_RESULTS,
            limitations=(
                "No external market-data provider is configured. Configure an authorized adapter for the requested metrics.",
            ),
        )
