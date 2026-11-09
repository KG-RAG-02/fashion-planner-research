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
from .domain.sales import (
    ComparabilityResult,
    MarketDataResult,
    MarketObservation,
    MissingData,
    SalesDataResult,
    SalesObservation,
    UnitEconomicsCalculation,
    UnitEconomicsResult,
    compare_sales_to_market,
)
from .sources.shared.composite import CompositeMarketResearchSource
from .sources.shared.noop import NoConfiguredSources
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
    "ComparabilityResult",
    "compare_sales_to_market",
    "MarketDataResult",
    "MarketObservation",
    "MissingData",
    "UnitEconomicsCalculation",
    "UnitEconomicsResult",
]
