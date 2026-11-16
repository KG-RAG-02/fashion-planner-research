"""Contracts and pure domain records for market research."""

from .models import (
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
from .sales import (
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

__all__ = [
    "AgentError",
    "EvidenceRecord",
    "FindingKind",
    "MarketResearchContext",
    "MarketResearchResult",
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

from .apparel_costing import calculate_benchmark_margin_estimate
from .sales_market_review import SalesMarketReview, build_sales_market_review

__all__ += ["SalesMarketReview", "build_sales_market_review", "calculate_benchmark_margin_estimate"]
