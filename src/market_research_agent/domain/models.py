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


def normalize_no_results_payload(value: Any) -> Any:
    """Map serialized no-results statuses to partial at outbound boundaries.

    Empty or unavailable evidence remains empty and is explained by the
    existing limitations/missing-data fields. This policy changes status
    labeling only; it never fabricates observations or findings.
    """
    if isinstance(value, dict):
        normalized = {key: normalize_no_results_payload(item) for key, item in value.items()}
        if normalized.get("status") == "no_results":
            normalized["status"] = "partial"
            limitations = normalized.get("limitations")
            if isinstance(limitations, list):
                note = "No usable evidence was returned for this scope; this partial result contains no implied zero value."
                if note not in limitations:
                    normalized["limitations"] = [*limitations, note]
        return normalized
    if isinstance(value, list):
        return [normalize_no_results_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_no_results_payload(item) for item in value)
    return value


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
    SYNTHETIC_TEST_ONLY = "authorized_for_synthetic_testing_only"


@dataclass(frozen=True, slots=True)
class MarketResearchContext:
    """Upstream context, kept opaque until shared input schemas are agreed."""

    request_id: str
    planning_request: dict[str, Any]
    trend_result: dict[str, Any] | None = None
    inventory_product_result: dict[str, Any] | None = None
    requested_category: str | None = None
    requested_metrics: tuple[str, ...] = ()
    requested_geography: str | None = None
    requested_channel: str | None = None
    requested_query: str | None = None
    requested_period: str | None = None
    # Optional local retrieval scope. These fields are not part of an approved
    # shared input contract; 1.0 output continues to omit requested_scope.
    requested_population: str | None = None
    requested_unit: str | None = None
    requested_currency: str | None = None
    requested_measure_type: str | None = None
    requested_mapping_id: str | None = None
    requested_mapping_version: str | None = None
    requested_period_grain: str | None = None
    requested_document_version: str | None = None
    inferred_scope: dict[str, Any] | None = None
    explicit_scope: dict[str, Any] | None = None
    query_intent: str = "unresolved"
    query_ambiguities: tuple[str, ...] = ()
    parser_version: str | None = None
    # The canonical orchestration request is an input-side proposal. It lets
    # the orchestrator own query interpretation without requiring Project_07
    # to re-parse the raw query or consume other agents' result payloads.
    canonical_request: dict[str, Any] | None = None


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
    synthetic: bool = False
    data_nature: str | None = None
    operational_eligible: bool | None = None


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
    sales_retrieval: Any | None = None
    market_retrieval: Any | None = None
    comparability: tuple[Any, ...] = ()
    upstream_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible object matching the local result schema."""
        payload = asdict(self)
        payload.pop("data_limitations", None)
        payload.pop("sales_data", None)
        payload.pop("market_data", None)
        payload.pop("unit_economics", None)
        payload.pop("requested_scope", None)
        payload.pop("upstream_context", None)
        payload.pop("sales_retrieval", None)
        payload.pop("market_retrieval", None)
        payload.pop("comparability", None)
        payload |= {
            "status": self.status.value,
            "findings": [
                asdict(item) | {"kind": item.kind.value}
                for item in self.findings
            ],
            "evidence": [
                asdict(item) | {"permission_status": item.permission_status.value}
                for item in self.evidence
                if item.permission_status != PermissionStatus.SYNTHETIC_TEST_ONLY
            ],
            "errors": [asdict(item) for item in self.errors],
        }
        if self.status == ResultStatus.NO_RESULTS:
            payload["summary"] = "Partial result: no eligible findings were returned; see limitations for the source gap."
        return normalize_no_results_payload(payload)

    def to_proposal_dict(self) -> dict[str, Any]:
        """Serialize the unratified 1.1 proposal without changing local 1.0."""
        payload = self.to_dict()
        payload["schema_version"] = "1.1-proposal"
        payload["sales_data"] = self.sales_data.to_dict() if self.sales_data else None
        if payload["sales_data"] is not None:
            payload["sales_data"].pop("missing_data", None)
            payload["sales_data"].pop("calculations", None)
        payload["data_limitations"] = [
            asdict(item) for item in self.data_limitations
        ]
        payload["evidence"] = [
            asdict(item) | {"permission_status": item.permission_status.value}
            for item in self.evidence
        ]
        return normalize_no_results_payload(payload)

    def to_proposal_v1_2_dict(self) -> dict[str, Any]:
        """Serialize the unratified separated-data proposal explicitly."""
        payload = self.to_dict()
        payload["schema_version"] = "1.2-proposal"
        payload["requested_scope"] = self.requested_scope or {}
        payload["upstream_context"] = self.upstream_context
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
        sales_block["observations"] = [
            asdict(item)
            for item in (self.sales_data.observations if self.sales_data else ())
        ]
        sales_block.pop("calculations", None)
        payload["market_data"] = (
            self.market_data.to_dict()
            if self.market_data
            else MarketDataResult(
                "no_results",
                limitations=("No market data source was configured.",),
                missing_data=(MissingData("market_data", "source_not_configured", "No market data source was configured."),),
            ).to_dict()
        )
        payload["market_data"].pop("calculations", None)
        # Proposal output can carry explicitly test-only synthetic Evidence;
        # stable 1.0 serialization intentionally omits it.
        payload["evidence"] = [
            asdict(item) | {"permission_status": item.permission_status.value}
            for item in self.evidence
        ]
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
        return normalize_no_results_payload(payload)

    def to_proposal_v1_3_dict(self) -> dict[str, Any]:
        """Serialize the explicit, unratified analysis and retrieval proposal."""
        payload = self.to_proposal_v1_2_dict()
        payload["schema_version"] = "1.3-proposal"
        for name, block in (("sales_data", self.sales_data), ("market_data", self.market_data)):
            payload[name]["calculations"] = [item.to_dict() for item in block.calculations] if block else []
        payload["sales_retrieval"] = self.sales_retrieval.to_dict() if self.sales_retrieval else None
        payload["market_retrieval"] = self.market_retrieval.to_dict() if self.market_retrieval else None
        payload["comparability"] = [asdict(item) for item in self.comparability]
        return normalize_no_results_payload(payload)
