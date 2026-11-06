"""Replaceable source adapters for authorized market information."""

from .composite import CompositeMarketResearchSource
from .noop import NoConfiguredSources
from .protocol import MarketResearchSource

__all__ = [
    "CompositeMarketResearchSource",
    "MarketResearchSource",
    "NoConfiguredSources",
]
