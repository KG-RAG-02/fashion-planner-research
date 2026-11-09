"""Replaceable source adapters for authorized market information."""

from .shared.composite import CompositeMarketResearchSource
from .shared.noop import NoConfiguredSources
from .shared.protocol import MarketResearchSource

__all__ = [
    "CompositeMarketResearchSource",
    "MarketResearchSource",
    "NoConfiguredSources",
]
