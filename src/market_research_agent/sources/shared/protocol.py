"""Provider port for market research; concrete providers remain unselected."""

from typing import Protocol

from ...domain.models import MarketResearchContext, SourceReport


class MarketResearchSource(Protocol):
    """An authorized source adapter; provider details are intentionally open."""

    def research(self, context: MarketResearchContext) -> SourceReport:
        """Return source-backed results for the supplied request context."""
