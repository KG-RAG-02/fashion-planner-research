"""Replaceable source adapters for authorized market information."""

from .noop import NoConfiguredSources
from .protocol import MarketResearchSource

__all__ = ["MarketResearchSource", "NoConfiguredSources"]
