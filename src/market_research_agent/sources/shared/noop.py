"""Safe source adapter used until a market source is approved and configured."""

from ...domain.models import MarketResearchContext, ResultStatus, SourceReport


class NoConfiguredSources:
    """Return an explicit no-results response without inventing market data."""

    def research(self, context: MarketResearchContext) -> SourceReport:
        return SourceReport(
            status=ResultStatus.NO_RESULTS,
            limitations=("No authorized market research source is configured.",),
        )
