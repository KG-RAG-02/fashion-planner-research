"""Internal post-planning review view, not a shared HTTP contract.

The adapter deliberately preserves the existing MarketResearchResult API and
does not assert that unavailable source data were analyzed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import MarketResearchResult


REVIEW_SCHEMA_VERSION = "sales-market-review/0.1-internal"
_UNAVAILABLE_SOURCES = (
    ("musinsa_prices", "No approved Musinsa price snapshot is connected."),
    ("fabric_costs", "No actual fabric-cost database is connected."),
    (
        "customer_segments",
        "No separately approved commercial customer aggregate is connected; H&M Kaggle customer data is out of scope.",
    ),
)


@dataclass(frozen=True, slots=True)
class SalesMarketReview:
    """Internal review envelope used while shared PlanningDraft contracts are pending."""

    schema_version: str
    request_id: str
    draft_id: str | None
    producer_id: str
    status: str
    category_price_comparison: tuple[dict[str, Any], ...]
    material_cost_analysis: tuple[dict[str, Any], ...]
    customer_segment_analysis: tuple[dict[str, Any], ...]
    margin_estimate: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    missing_data: tuple[dict[str, Any], ...]
    errors: tuple[dict[str, Any], ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "draft_id": self.draft_id,
            "producer_id": self.producer_id,
            "status": self.status,
            "category_price_comparison": list(self.category_price_comparison),
            "material_cost_analysis": list(self.material_cost_analysis),
            "customer_segment_analysis": list(self.customer_segment_analysis),
            "margin_estimate": self.margin_estimate,
            "evidence": list(self.evidence),
            "missing_data": list(self.missing_data),
            "errors": list(self.errors),
            "limitations": list(self.limitations),
        }


def _draft_id(draft: dict[str, Any]) -> str | None:
    """Read only stable identifier aliases; never inspect or serialize draft PII."""
    for field in ("draft_id", "planning_draft_id", "id"):
        value = draft.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def build_sales_market_review(
    result: MarketResearchResult,
    *,
    planning_draft: dict[str, Any],
) -> SalesMarketReview:
    """Adapt an existing result into an internal review shape.

    Draft fields are intentionally not guessed. The draft is used only to
    extract a stable identifier; product attributes require an approved schema
    before they can drive source queries or calculations.
    """
    if not isinstance(planning_draft, dict):
        raise TypeError("planning_draft must be an object.")
    if not isinstance(result, MarketResearchResult):
        raise TypeError("result must be a MarketResearchResult.")

    missing = [
        {"field": field, "reason": "source_not_configured", "message": message}
        for field, message in _UNAVAILABLE_SOURCES
    ]
    unit = result.unit_economics
    margin = (
        unit.to_dict()
        if unit is not None
        else {
            "status": "no_results",
            "calculations": [],
            "limitations": ["No approved margin formula and complete unit-cost inputs are available."],
            "errors": [],
            "missing_data": [{
                "field": "margin_estimate",
                "reason": "source_not_configured",
                "message": "A complete, approved unit cost and margin formula are required.",
            }],
        }
    )
    limitations = list(result.limitations)
    if result.status.value in {"ok", "partial"}:
        limitations.append(
            "This internal review adapter is not an approved shared SalesMarketReview contract."
        )
    return SalesMarketReview(
        schema_version=REVIEW_SCHEMA_VERSION,
        request_id=result.request_id,
        draft_id=_draft_id(planning_draft),
        producer_id=result.agent_id,
        status=result.status.value,
        category_price_comparison=(),
        material_cost_analysis=(),
        customer_segment_analysis=(),
        margin_estimate=margin,
        evidence=tuple(
            {
                "evidence_id": item.evidence_id,
                "source_id": item.source_id,
                "source_type": item.source_type,
                "source_name": item.source_name,
                "locator": item.locator,
                "observed_at": item.observed_at,
                "as_of": item.as_of,
                "permission_status": item.permission_status.value,
            }
            for item in result.evidence
        ),
        missing_data=tuple(missing),
        errors=tuple({"code": item.code, "message": item.message} for item in result.errors),
        limitations=tuple(dict.fromkeys(limitations)),
    )
