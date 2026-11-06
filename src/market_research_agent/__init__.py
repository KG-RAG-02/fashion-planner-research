"""Public API for the VOGUE Market Research Agent."""

from .domain.models import (
    AgentError,
    EvidenceRecord,
    FindingKind,
    MarketResearchContext,
    MarketResearchResult,
    PermissionStatus,
    ResearchFinding,
    ResultStatus,
    SourceReport,
)
from .domain.sales import SalesDataResult, SalesObservation
from .sources.composite import CompositeMarketResearchSource
from .sources.noop import NoConfiguredSources
from .workflows.market_research import MarketResearchAgent

__all__ = [
    "AgentError",
    "CompositeMarketResearchSource",
    "EvidenceRecord",
    "FindingKind",
    "MarketResearchAgent",
    "MarketResearchContext",
    "MarketResearchResult",
    "NoConfiguredSources",
    "PermissionStatus",
    "ResearchFinding",
    "ResultStatus",
    "SourceReport",
    "SalesDataResult",
    "SalesObservation",
]
