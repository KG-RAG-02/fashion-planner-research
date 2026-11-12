"""Public API for the VOGUE Market Research Agent."""

from .domain.apparel_costing import CostingInputError, calculate_apparel_margin, validate_apparel_costing
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
from .domain.sales_market_review import SalesMarketReview, build_sales_market_review
from .sources.shared.noop import NoConfiguredSources
from .workflows.market_research import MarketResearchAgent
from .orchestration_request import CanonicalRequestError, context_from_canonical_request

__all__ = [
    "AgentError",
    "CanonicalRequestError",
    "CompositeMarketResearchSource",
    "EvidenceRecord",
    "FindingKind",
    "MarketResearchAgent",
    "context_from_canonical_request",
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
    "SalesMarketReview",
    "build_sales_market_review",
    "CostingInputError",
    "calculate_apparel_margin",
    "validate_apparel_costing",
]
