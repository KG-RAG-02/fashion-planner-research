"""Internal post-planning review view, not a shared HTTP contract.

The adapter deliberately preserves the existing MarketResearchResult API and
does not assert that unavailable source data were analyzed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Any

from .apparel_costing import (
    calculate_apparel_margin,
    calculate_benchmark_margin_estimate,
    calculate_price_range_margin_analysis,
)
from .models import MarketResearchResult


REVIEW_SCHEMA_VERSION = "sales-market-review/0.1-internal"


def _fabric_reference(fabric_rows: tuple[dict[str, Any], ...]) -> tuple[float | None, str | None, list[str]]:
    """Return the median matched KRW/m listing reference and its record IDs."""
    values: list[tuple[float, str | None]] = []
    for group in fabric_rows:
        for item in group.get("sources", []):
            amount = item.get("unit_price_krw_per_meter")
            if isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount) and amount > 0:
                values.append((float(amount), item.get("record_id")))
    if not values:
        for group in fabric_rows:
            stats = group.get("price_per_meter_krw_statistics")
            amount = stats.get("median") if isinstance(stats, dict) else None
            if isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount) and amount > 0:
                values.append((float(amount), None))
    if not values:
        return None, None, []
    return median(value for value, _record_id in values), "KRW/m", [
        record_id for _value, record_id in values if isinstance(record_id, str) and record_id
    ]


def _hm_suggested_price(prices: dict[str, Any] | None) -> float | None:
    """Return the mean on the user-selected USD ×590 reference scale."""
    if not prices:
        return None
    mean = prices.get("scaled_price_statistics", {}).get("mean")
    if mean is None:
        raw_mean = prices.get("price_statistics", {}).get("mean")
        mean = raw_mean * 590 if isinstance(raw_mean, (int, float)) and not isinstance(raw_mean, bool) else None
    if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not math.isfinite(mean) or mean <= 0:
        return None
    return round(float(mean), 2)


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

    @property
    def decision_support(self) -> dict[str, Any]:
        """Summarize evidence coverage for the orchestrator; never decide for MD."""
        return {
            "recommendation": "review_required",
            "ready_for_human_decision": self.status in {"ok", "partial"},
            "evidence_areas": {
                "category_price": bool(self.category_price_comparison),
                "material_cost": any(
                    row.get("source_type") != "planning_draft_costing_input"
                    and row.get("verified_price_per_meter_available") is True
                    for row in self.material_cost_analysis
                ),
                "margin": self.margin_estimate.get("status") == "ok",
                "customer_segments": bool(self.customer_segment_analysis),
            },
            "human_decision_required": True,
        }

    def to_dict(self) -> dict[str, Any]:
        prices = self.category_price_comparison[0] if self.category_price_comparison else None
        price_stats = prices.get("price_statistics", {}) if prices else {}
        scaled_stats = prices.get("scaled_price_statistics", {}) if prices else {}
        usd_krw = prices.get("usd_krw_conversion", {}) if prices else {}
        proposed_price = usd_krw.get("suggested_price_krw")
        price_analysis = {
            "status": "ok" if proposed_price is not None else "reference_only" if prices else "no_results",
            "hm_average_price_usd": _hm_suggested_price(prices),
            "raw_normalized_mean": price_stats.get("mean"),
            "currency": "KRW" if proposed_price is not None else None,
            "source_currency": scaled_stats.get("currency"),
            "unit": "KRW" if proposed_price is not None else scaled_stats.get("unit"),
            "scale_factor": scaled_stats.get("scale_factor"),
            "converted_average_krw": usd_krw.get("converted_amount_krw"),
            "exchange_rate": {
                key: usd_krw.get(key) for key in
                ("base_currency", "quote_currency", "rate", "rate_date", "provider", "rate_type",
                 "fallback_used", "fallback_age_days", "stale")
            } if usd_krw and usd_krw.get("status") != "error" else None,
            "sample_size": prices.get("sample_count") if prices else None,
            "period": prices.get("period") if prices else None,
            "proposed_price": proposed_price,
            "method": "normalized source mean × 590 as USD; convert using dated USD/KRW reference rate; round suggested KRW price to nearest 1,000",
            "proposal_reason": (
                "The ×590-to-USD interpretation is a user-selected research scale, not official Kaggle currency metadata."
                if proposed_price is not None else
                usd_krw.get("message") if usd_krw.get("status") == "error" else
                "No matching H&M price observations are available." if not prices else
                "A USD/KRW reference rate is unavailable; no KRW proposal was derived."
            ),
            "limitations": list(prices.get("limitations", [])) if prices else [],
        }
        fabric_cost_analysis = {
            "status": "reference_only" if self.material_cost_analysis else "no_results",
            "unit_cost": None,
            "unit": None,
            "currency": None,
            "matched_material": self.draft_context.get("material"),
            "source_record_ids": [],
            "price_references": list(self.material_cost_analysis),
            "limitations": ["Fabric listing references are not verified purchase costs; no verified per-meter cost is asserted."],
        }
        fabric_reference_cost, fabric_reference_unit, fabric_reference_ids = _fabric_reference(
            self.material_cost_analysis,
        )
        if fabric_reference_cost is not None:
            fabric_cost_analysis.update({
                "unit_cost": round(fabric_reference_cost, 2),
                "unit": fabric_reference_unit,
                "currency": "KRW",
                "source_record_ids": fabric_reference_ids,
                "status": "reference_only",
            })
        margin_analysis = {
            "status": self.margin_estimate.get("status", "no_results"),
            "basis": "complete_apparel_costing_sheet" if self.margin_estimate.get("status") == "ok" else self.margin_estimate.get("basis"),
            "formula": (
                self.margin_estimate.get("formula", {}).get("gross_margin_rate")
                if isinstance(self.margin_estimate.get("formula"), dict)
                else self.margin_estimate.get("formula")
            ) or (
                "estimated_gross_margin_rate = NYU Stern US apparel gross margin benchmark"
                if self.margin_estimate.get("status") in {"benchmark_estimate", "benchmark_only"}
                else None
            ),
            "selling_price": self.margin_estimate.get("basis_price", self.margin_estimate.get("selling_price")),
            "currency": self.margin_estimate.get("currency"),
            "margin_rate_percent": (
                round(self.margin_estimate["calculation"]["gross_margin_rate"] * 100, 4)
                if self.margin_estimate.get("status") == "ok" else
                round(self.margin_estimate["benchmark_gross_margin_rate"] * 100, 2)
                if self.margin_estimate.get("status") in {"benchmark_estimate", "benchmark_only"}
                and isinstance(self.margin_estimate.get("benchmark_gross_margin_rate"), (int, float)) else None
            ),
            "estimate_type": self.margin_estimate.get("estimate_type", "caller_supplied_cost_sheet"),
        }
        if self.margin_estimate.get("status") == "benchmark_estimate":
            margin_analysis.update({
                "benchmark_gross_margin_rate_percent": round(self.margin_estimate["benchmark_gross_margin_rate"] * 100, 2),
                "benchmark_source": self.margin_estimate.get("benchmark_source"),
                "benchmark_sample_size": self.margin_estimate.get("benchmark_sample_size"),
                "benchmark_region": self.margin_estimate.get("benchmark_region"),
                "benchmark_cost_ratio_percent": round((1 - self.margin_estimate["benchmark_gross_margin_rate"]) * 100, 2),
                **({"implied_total_cost_allowance": self.margin_estimate["implied_total_cost_allowance"]}
                   if self.margin_estimate.get("implied_total_cost_allowance") is not None else {}),
                **({"fabric_reference_cost_per_unit": self.margin_estimate["fabric_unit_cost"],
                    "fabric_reference_unit": self.margin_estimate.get("fabric_unit")}
                   if self.margin_estimate.get("fabric_unit_cost") is not None else {}),
                **({"fabric_cost_per_garment": self.margin_estimate["fabric_cost_per_garment"]}
                   if self.margin_estimate.get("fabric_cost_per_garment") is not None else {}),
                **({"remaining_cost_allowance_after_fabric": self.margin_estimate["remaining_cost_allowance_after_fabric"]}
                   if self.margin_estimate.get("remaining_cost_allowance_after_fabric") is not None else {}),
                "limitations": self.margin_estimate.get("limitations", []),
            })
        range_margin_analysis = None
        fx_rate = usd_krw.get("rate") if isinstance(usd_krw, dict) else None
        scaled_stats = prices.get("scaled_price_statistics", {}) if prices else {}
        if (scaled_stats and isinstance(fx_rate, (int, float)) and not isinstance(fx_rate, bool)
                and fabric_reference_cost is not None and fabric_reference_unit == "KRW/m"):
            range_margin_analysis = calculate_price_range_margin_analysis(
                scaled_price_statistics=scaled_stats,
                usd_krw_rate=fx_rate,
                fabric_unit_cost=fabric_reference_cost,
                fabric_unit=fabric_reference_unit,
                fx_rate_date=usd_krw.get("rate_date"),
                fx_provider=usd_krw.get("provider"),
                fx_fallback_used=bool(usd_krw.get("fallback_used", False)),
                fx_rate_stale=bool(usd_krw.get("stale", False)),
                fabric_reference_record_ids=fabric_reference_ids,
            )
            margin_analysis["price_range_comparison"] = range_margin_analysis
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
            "decision_support": self.decision_support,
            "price_analysis": price_analysis,
            "fabric_cost_analysis": fabric_cost_analysis,
            "margin_analysis": margin_analysis,
            "margin_price_range_analysis": range_margin_analysis,
            "target_audience": self.target_audience_analysis,
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
    fabric_price_analysis: tuple[dict[str, Any], ...] = (),
    fabric_price_error: str | None = None,
    historical_price_analysis: dict[str, Any] | None = None,
    historical_price_error: str | None = None,
    customer_age_profile: dict[str, Any] | None = None,
    customer_age_error: str | None = None,
    image_style_analysis: dict[str, Any] | None = None,
    image_style_error: str | None = None,
    image_style_requested: bool = False,
    target_audience_proposal: dict[str, Any] | None = None,
) -> SalesMarketReview:
    """Adapt an existing result into an internal review shape.

    Product attributes are taken only from validated PlanningDraft fields.
    Fabric and historical H&M price analyses are supplied by read-only R2 sources.
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
    if fabric_price_error:
        errors.append({"code": "R2_FABRIC_PRICE_READ_FAILED", "message": fabric_price_error})
    if image_style_error:
        errors.append({"code": "IMAGE_STYLE_ANALYSIS_FAILED", "message": image_style_error})
    missing: list[dict[str, Any]] = []
    fabrics = list(fabric_price_analysis)
    if historical_price_analysis is None:
        missing.append({"field": "historical_hm_transaction_prices",
                        "reason": "source_error" if historical_price_error else "source_not_configured_or_disabled",
                        "message": historical_price_error or "Enable non-commercial H&M research mode and configure its R2 source."})
    if not fabrics:
        reason = "source_error" if fabric_price_error else "material_not_specified" if not product.get("material") else "source_not_configured_or_no_match"
        missing.append({"field": "fabric_costs", "reason": reason,
                        "message": fabric_price_error or "No R2 fabric listing prices matched the supplied material; per-meter cost requires a verified sales unit."})
    if customer_age_profile is None:
        missing.append({"field": "customer_segments",
                        "reason": "source_error" if customer_age_error else "source_not_configured_or_disabled",
                        "message": customer_age_error or "Enable non-commercial H&M research mode and configure its R2 source."})
    if image_style_requested and image_style_analysis is None:
        missing.append({"field": "image_style_analysis",
                        "reason": "analysis_error" if image_style_error else "not_requested_or_not_authorized",
                        "message": image_style_error or "Request image style analysis explicitly and enable image egress authorization."})
    costing = planning_draft.get("costing")
    margin_price = draft_context.get("proposed_price")
    margin_currency = draft_context.get("currency")
    if margin_price is None and historical_price_analysis is not None:
        conversion = historical_price_analysis.get("usd_krw_conversion", {})
        margin_price = conversion.get("suggested_price_krw")
        if margin_price is not None:
            margin_currency = "KRW"
    if costing is not None:
        margin = calculate_apparel_margin(
            proposed_price=margin_price,
            price_currency=margin_currency,
            costing=costing,
        )
    else:
        fabric_unit_cost, fabric_unit, fabric_record_ids = _fabric_reference(tuple(fabrics))
        margin = calculate_benchmark_margin_estimate(
            selling_price=margin_price, currency=margin_currency,
            fabric_unit_cost=fabric_unit_cost, fabric_unit=fabric_unit,
        )
        if fabric_record_ids:
            margin["fabric_reference_record_ids"] = fabric_record_ids
        if margin_price is None:
            margin["status"] = "benchmark_only"
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
    if margin.get("status") in {"benchmark_estimate", "benchmark_only"}:
        review_status = "partial"
        limitations.append("Margin analysis uses the US apparel industry gross-margin benchmark; it is not this product's actual gross margin.")
    if historical_price_analysis is not None:
        review_status = "partial"
        limitations.append("H&M Kaggle transaction prices are historical non-commercial research references, not current market listing prices.")
    if fabrics:
        review_status = "partial"
        limitations.append("R2 fabric listing prices are public snapshot references and are not confirmed per-meter production costs.")
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
        else "partial" if customer_age_profile is not None or image_style_analysis is not None or target_audience_proposal is not None
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
            "proposal": target_audience_proposal,
            "age_cohort": customer_age_profile.get("target_age_cohort") if customer_age_profile else None,
            "style_analysis": image_style_analysis,
            "target_sentence": target_sentence,
            "limitations": list(dict.fromkeys(
                (["Age profile is unavailable or disabled."] if customer_age_profile is None else [])
                + (["Image style analysis was not requested, authorized, or completed."] if image_style_analysis is None else [])
                + (["LLM target hypothesis is unavailable or not authorized."] if target_audience_proposal is None else [])
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
