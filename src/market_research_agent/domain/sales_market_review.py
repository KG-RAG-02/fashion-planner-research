"""Internal post-planning review view, not a shared HTTP contract.

The adapter deliberately preserves the existing MarketResearchResult API and
does not assert that unavailable source data were analyzed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .apparel_costing import calculate_apparel_margin
from .models import MarketResearchResult
from .price_references import SnapshotError, analyze_fabric_snapshot


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
    target_audience_analysis: dict[str, Any]
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
            "target_audience_analysis": self.target_audience_analysis,
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


_AGE_LABELS_KO = {
    "under_20": "20세 미만", "20_29": "20대", "30_39": "30대",
    "40_49": "40대", "50_plus": "50대 이상",
}
_STYLE_LABELS_KO = {
    "datewear": "데이트룩", "officewear": "오피스룩", "casualwear": "캐주얼룩",
    "sportwear": "스포츠룩", "formalwear": "포멀룩", "occasionwear": "행사·파티룩",
    "streetwear": "스트리트룩", "other": "기타 스타일", "uncertain": "판단이 불확실한 스타일",
}


def _target_sentence(profile: dict[str, Any], style: dict[str, Any], product: dict[str, Any]) -> str | None:
    cohort = profile.get("target_age_cohort")
    cohort_label = {"under_20": "20세 미만", "20_39": "20~30대", "40_plus": "40대 이상"}
    if isinstance(cohort, dict) and cohort.get("age_band") in cohort_label and not cohort.get("suppressed"):
        age_text = cohort_label[cohort["age_band"]]
    else:
        age_text = None
    bands = [item for item in profile.get("age_bands", [])
             if item.get("age_band") in _AGE_LABELS_KO and item.get("unique_customer_count") is not None]
    bands.sort(key=lambda item: item["unique_customer_count"], reverse=True)
    if not bands or not age_text:
        return None
    gender = {"women": "여성용", "men": "남성용"}.get(str(product.get("gender", "")).casefold(), "")
    style_text = _STYLE_LABELS_KO.get(style.get("style_label"), "스타일")
    target_phrase = " ".join(part for part in (age_text, gender, style_text) if part)
    return (
        f"H&M 과거 연구 자료에서 이 카테고리의 {age_text} 연령대 비중이 가장 높게 관측되었습니다. "
        f"이미지 스타일 해석을 결합한 비상업적 연구 가설은 '{target_phrase}'입니다."
    )


def build_sales_market_review(
    result: MarketResearchResult,
    *,
    planning_draft: dict[str, Any],
    historical_price_analysis: dict[str, Any] | None = None,
    historical_price_error: str | None = None,
    customer_age_profile: dict[str, Any] | None = None,
    customer_age_error: str | None = None,
    image_style_analysis: dict[str, Any] | None = None,
    image_style_error: str | None = None,
    image_style_requested: bool = False,
) -> SalesMarketReview:
    """Adapt an existing result into an internal review shape.

    Product attributes are taken only from validated PlanningDraft fields.
    Fabric snapshot adapters are offline CSV readers. Historical H&M prices are
    supplied by the read-only R2 research source.
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
    if customer_age_error:
        errors.append({"code": "CUSTOMER_AGE_RESEARCH_FAILED", "message": customer_age_error})
    if historical_price_error:
        errors.append({"code": "HM_HISTORICAL_PRICE_RESEARCH_FAILED", "message": historical_price_error})
    if image_style_error:
        errors.append({"code": "IMAGE_STYLE_ANALYSIS_FAILED", "message": image_style_error})
    missing: list[dict[str, Any]] = []
    fabric_error = False
    try:
        fabrics = analyze_fabric_snapshot(product)
    except SnapshotError as exc:
        fabrics = []
        fabric_error = True
        errors.append({"code": "FABRIC_SNAPSHOT_INVALID", "message": str(exc)})
    if historical_price_analysis is None:
        missing.append({"field": "historical_hm_transaction_prices",
                        "reason": "source_error" if historical_price_error else "source_not_configured_or_disabled",
                        "message": historical_price_error or "Enable non-commercial H&M research mode and configure its R2 source."})
    if not fabrics:
        reason = "source_error" if fabric_error else "material_not_specified" if not product.get("material") else "source_not_configured_or_no_match"
        missing.append({"field": "fabric_costs", "reason": reason,
                        "message": "Set FABRIC_PRICE_SNAPSHOT_PATH and supply an exact material match; source prices remain references."})
    if customer_age_profile is None:
        missing.append({"field": "customer_segments",
                        "reason": "source_error" if customer_age_error else "source_not_configured_or_disabled",
                        "message": customer_age_error or "Enable non-commercial H&M research mode and configure its R2 source."})
    if image_style_requested and image_style_analysis is None:
        missing.append({"field": "image_style_analysis",
                        "reason": "analysis_error" if image_style_error else "not_requested_or_not_authorized",
                        "message": image_style_error or "Request image style analysis explicitly and enable image egress authorization."})
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
    if historical_price_analysis is not None:
        review_status = "partial"
        limitations.append("H&M Kaggle transaction prices are historical non-commercial research references, not current market listing prices.")
    if fabrics:
        review_status = "partial"
    if customer_age_profile is not None:
        review_status = "partial"
        limitations.append("H&M age-band profiles are historical non-commercial research only; they are not current VOGUE customer behavior or demand.")
    if image_style_analysis is not None and review_status in {"no_results", "error"}:
        review_status = "partial"
    target_sentence = (
        _target_sentence(customer_age_profile, image_style_analysis, product)
        if customer_age_profile is not None and image_style_analysis is not None else None
    )
    target_audience_status = (
        "ok" if customer_age_profile is not None and image_style_analysis is not None
        else "partial" if customer_age_profile is not None or image_style_analysis is not None
        else "no_results"
    )
    return SalesMarketReview(
        schema_version=REVIEW_SCHEMA_VERSION,
        request_id=result.request_id,
        draft_id=_draft_id(planning_draft),
        producer_id=result.agent_id,
        status=review_status,
        draft_context=draft_context,
        category_price_comparison=(historical_price_analysis,) if historical_price_analysis else (),
        material_cost_analysis=material_rows,
        customer_segment_analysis=(
            (customer_age_profile,) if customer_age_profile is not None else ()
        ),
        target_audience_analysis={
            "status": target_audience_status,
            "age_cohort": customer_age_profile.get("target_age_cohort") if customer_age_profile else None,
            "style_analysis": image_style_analysis,
            "target_sentence": target_sentence,
            "limitations": list(dict.fromkeys(
                (["Age profile is unavailable or disabled."] if customer_age_profile is None else [])
                + (["Image style analysis was not requested, authorized, or completed."] if image_style_analysis is None else [])
            )),
        },
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
