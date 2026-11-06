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
from .sales import SalesDataResult, SalesObservation

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
]
