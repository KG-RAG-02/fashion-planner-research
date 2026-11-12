"""Internal post-planning review view, not a shared HTTP contract.

The adapter deliberately preserves the existing MarketResearchResult API and
does not assert that unavailable source data were analyzed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .apparel_costing import calculate_apparel_margin
from .models import MarketResearchResult
from .price_references import SnapshotError, analyze_fabric_snapshot, analyze_musinsa_snapshot


REVIEW_SCHEMA_VERSION = "sales-market-review/0.1-internal"


@dataclass(frozen=True, slots=True)
class SalesMarketReview:
    """Internal review envelope used while shared PlanningDraft contracts are pending."""

    schema_version: str
    request_id: str
    draft_id: str | None
    producer_id: str
    status: str
    draft_context: dict[str, Any]
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
            "draft_context": self.draft_context,
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


def _draft_context(draft: dict[str, Any]) -> dict[str, Any]:
    """Return only the allowlisted non-personal product fields."""
    product = draft.get("product")
    if not isinstance(product, dict):
        return {}
    allowed = ("category", "product_type", "gender", "season", "material")
    context = {
        key: product[key]
        for key in allowed
        if isinstance(product.get(key), str) and product[key].strip()
    }
    if isinstance(context.get("gender"), str):
        context["gender"] = context["gender"].casefold()
    price = product.get("proposed_price")
    currency = product.get("currency")
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        context["proposed_price"] = price
    if isinstance(currency, str) and currency.strip():
        context["currency"] = currency.strip().upper()
    return context


def build_sales_market_review(
    result: MarketResearchResult,
    *,
    planning_draft: dict[str, Any],
) -> SalesMarketReview:
    """Adapt an existing result into an internal review shape.

    Product attributes are taken only from validated PlanningDraft fields.
    Snapshot adapters are offline CSV readers and never fetch external pages.
    """
    if not isinstance(planning_draft, dict):
        raise TypeError("planning_draft must be an object.")
    if not isinstance(result, MarketResearchResult):
        raise TypeError("result must be a MarketResearchResult.")

    unit = result.unit_economics
    draft_context = _draft_context(planning_draft)
    product = planning_draft.get("product") if isinstance(planning_draft.get("product"), dict) else {}
    errors: list[dict[str, Any]] = [
        {"code": item.code, "message": item.message} for item in result.errors
    ]
    missing: list[dict[str, Any]] = []
    musinsa_error = False
    try:
        musinsa = analyze_musinsa_snapshot(product)
    except SnapshotError as exc:
        musinsa = None
        musinsa_error = True
        errors.append({"code": "MUSINSA_SNAPSHOT_INVALID", "message": str(exc)})
    fabric_error = False
    try:
        fabrics = analyze_fabric_snapshot(product)
    except SnapshotError as exc:
        fabrics = []
        fabric_error = True
        errors.append({"code": "FABRIC_SNAPSHOT_INVALID", "message": str(exc)})
    if musinsa is None:
        missing.append({"field": "musinsa_prices", "reason": "source_error" if musinsa_error else "source_not_configured",
                         "message": "Configured Musinsa snapshot could not be read." if musinsa_error else "Set MUSINSA_PRICE_SNAPSHOT_PATH to a permitted CSV snapshot."})
    elif not musinsa["comparables"]:
        missing.append({"field": "musinsa_prices", "reason": "no_matching_observations",
                         "message": "No authorized snapshot rows exactly match the product category and supplied filters."})
    if not fabrics:
        reason = "source_error" if fabric_error else "material_not_specified" if not product.get("material") else "source_not_configured_or_no_match"
        missing.append({"field": "fabric_costs", "reason": reason,
                        "message": "Set FABRIC_PRICE_SNAPSHOT_PATH and supply an exact material match; source prices remain references."})
    missing.append({"field": "customer_segments", "reason": "source_not_configured",
                    "message": "No separate authorized aggregate customer CSV is configured. H&M Kaggle customer data is not used."})
    costing = planning_draft.get("costing")
    if costing is not None:
        margin = calculate_apparel_margin(
            proposed_price=draft_context.get("proposed_price"),
            price_currency=draft_context.get("currency"),
            costing=costing,
        )
    elif unit is not None:
        margin = unit.to_dict()
    else:
        margin = calculate_apparel_margin(
            proposed_price=draft_context.get("proposed_price"),
            price_currency=draft_context.get("currency"),
            costing=None,
        )
    limitations = list(result.limitations)
    if result.status.value in {"ok", "partial"}:
        limitations.append(
            "This internal review adapter is not an approved shared SalesMarketReview contract."
        )
    review_status = result.status.value
    if review_status in {"no_results", "error"} and margin.get("status") == "ok":
        review_status = "partial"
    material_rows = tuple(
        {
            **item,
            "source_type": "planning_draft_costing_input",
            "source_verification": "caller_supplied_not_independently_verified",
        }
        for item in margin.get("materials", [])
    )
    if fabrics:
        material_rows += tuple(fabrics)
    if musinsa and musinsa["comparables"]:
        review_status = "partial"
    if fabrics:
        review_status = "partial"
    return SalesMarketReview(
        schema_version=REVIEW_SCHEMA_VERSION,
        request_id=result.request_id,
        draft_id=_draft_id(planning_draft),
        producer_id=result.agent_id,
        status=review_status,
        draft_context=draft_context,
        category_price_comparison=(musinsa,) if musinsa and musinsa["comparables"] else (),
        material_cost_analysis=material_rows,
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
        errors=tuple(errors),
        limitations=tuple(dict.fromkeys(limitations)),
    )
