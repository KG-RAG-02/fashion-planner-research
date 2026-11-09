"""Contract-neutral domain records for evidence-backed market research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from .sales import (
    MarketDataResult,
    MissingData,
    SalesDataResult,
    UnitEconomicsResult,
)


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
    requested_category: str | None = None
    requested_metrics: tuple[str, ...] = ()
    requested_geography: str | None = "KR"
    requested_channel: str | None = "online"


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
    sales_data: SalesDataResult | None = None
    market_data: MarketDataResult | None = None
    unit_economics: UnitEconomicsResult | None = None


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
    sales_data: SalesDataResult | None = None
    market_data: MarketDataResult | None = None
    unit_economics: UnitEconomicsResult | None = None
    requested_scope: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible object matching the local result schema."""
        payload = asdict(self)
        payload.pop("data_limitations", None)
        payload.pop("sales_data", None)
        payload.pop("market_data", None)
        payload.pop("unit_economics", None)
        payload.pop("requested_scope", None)
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

    def to_proposal_dict(self) -> dict[str, Any]:
        """Serialize the unratified 1.1 proposal without changing local 1.0."""
        payload = self.to_dict()
        payload["schema_version"] = "1.1-proposal"
        payload["sales_data"] = self.sales_data.to_dict() if self.sales_data else None
        if payload["sales_data"] is not None:
            payload["sales_data"].pop("missing_data", None)
        payload["data_limitations"] = [
            asdict(item) for item in self.data_limitations
        ]
        payload["evidence"] = [
            asdict(item) | {"permission_status": item.permission_status.value}
            for item in self.evidence
        ]
        return payload

    def to_proposal_v1_2_dict(self) -> dict[str, Any]:
        """Serialize the unratified separated-data proposal explicitly."""
        payload = self.to_dict()
        payload["schema_version"] = "1.2-proposal"
        payload["requested_scope"] = self.requested_scope or {}
        sales_block = (
            self.sales_data.to_dict()
            if self.sales_data
            else SalesDataResult(
                "no_results",
                limitations=("No sales source was configured.",),
                missing_data=(MissingData("sales_data", "source_not_configured", "No sales source was configured."),),
            ).to_dict()
        )
        sales_block["observations"] = [
            asdict(item) | {"measure_type": "transaction_actual"}
            for item in (self.sales_data.observations if self.sales_data else ())
        ]
        payload["sales_data"] = sales_block
        payload["market_data"] = (
            self.market_data.to_dict()
            if self.market_data
            else MarketDataResult(
                "no_results",
                limitations=("No market data source was configured.",),
                missing_data=(MissingData("market_data", "source_not_configured", "No market data source was configured."),),
            ).to_dict()
        )
        payload["unit_economics"] = (
            self.unit_economics.to_dict()
            if self.unit_economics
            else UnitEconomicsResult(
                "no_results",
                limitations=("No approved unit-economics formula and inputs were configured.",),
                missing_data=(MissingData("unit_economics", "source_not_configured", "No approved formula and inputs were configured."),),
            ).to_dict()
        )
        payload["missing_data"] = [
            item
            for block in (payload["sales_data"], payload["market_data"], payload["unit_economics"])
            for item in block["missing_data"]
        ]
        return payload
