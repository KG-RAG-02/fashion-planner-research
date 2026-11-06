"""Contract-neutral domain records for evidence-backed market research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ResultStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    NO_RESULTS = "no_results"
    ERROR = "error"


class FindingKind(StrEnum):
    FACT = "fact"
    INTERPRETATION = "interpretation"
    HYPOTHESIS = "hypothesis"
    PROPOSAL = "proposal"


class PermissionStatus(StrEnum):
    AUTHORIZED = "authorized"
    UNKNOWN = "unknown"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class MarketResearchContext:
    """Upstream context, kept opaque until shared input schemas are agreed."""

    request_id: str
    planning_request: dict[str, Any]
    trend_result: dict[str, Any] | None = None
    inventory_product_result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    source_id: str
    source_type: str
    source_name: str
    locator: str | None = None
    published_at: str | None = None
    observed_at: str | None = None
    fetched_at: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    as_of: str | None = None
    geography: str | None = None
    channel: str | None = None
    product_mapping: str | None = None
    unit: str | None = None
    currency: str | None = None
    metric_definition: str | None = None
    calculation_reference: str | None = None
    permission_status: PermissionStatus = PermissionStatus.UNKNOWN
    manifest_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchFinding:
    finding_id: str
    topic: str
    statement: str
    kind: FindingKind
    evidence_ids: tuple[str, ...] = ()
    related_concept_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DataLimitation:
    """Structured issue detail; codes are local proposals until shared agreement."""

    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()
    field: str | None = None


@dataclass(frozen=True, slots=True)
class SourceReport:
    """One provider's report before the agent applies provenance checks."""

    status: ResultStatus
    findings: tuple[ResearchFinding, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    limitations: tuple[str, ...] = ()
    errors: tuple[AgentError, ...] = ()
    data_limitations: tuple[DataLimitation, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketResearchResult:
    schema_version: str
    request_id: str
    agent_id: str
    status: ResultStatus
    summary: str
    findings: tuple[ResearchFinding, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    limitations: tuple[str, ...] = ()
    errors: tuple[AgentError, ...] = ()
    data_limitations: tuple[DataLimitation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible object matching the local result schema."""
        payload = asdict(self)
        payload.pop("data_limitations", None)
        payload |= {
            "status": self.status.value,
            "findings": [
                asdict(item) | {"kind": item.kind.value}
                for item in self.findings
            ],
            "evidence": [
                asdict(item) | {"permission_status": item.permission_status.value}
                for item in self.evidence
            ],
            "errors": [asdict(item) for item in self.errors],
        }
        return payload
