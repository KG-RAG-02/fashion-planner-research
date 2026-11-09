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
    MarketDataResult,
    MarketObservation,
    MissingData,
    SalesDataResult,
    SalesObservation,
    UnitEconomicsCalculation,
    UnitEconomicsResult,
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
    "MarketDataResult",
    "MarketObservation",
    "MissingData",
    "UnitEconomicsCalculation",
    "UnitEconomicsResult",
]
