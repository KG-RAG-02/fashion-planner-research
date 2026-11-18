"""Review result of the research service and its source-backed assembly."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from statistics import median
from typing import Any

from .apparel_costing import (
    calculate_benchmark_margin_estimate,
    calculate_price_range_margin_analysis,
)
REVIEW_SCHEMA_VERSION = "sales-market-review/0.1-internal"


def _fabric_reference(fabric_rows: tuple[dict[str, Any], ...]) -> tuple[float | None, str | None, list[str]]:
    """Return the median matched KRW/m listing reference and its record IDs."""
    values: list[tuple[float, str | None]] = []
    for group in fabric_rows:
        for item in group.get("sources", []):
            if item.get("unit_price_estimation_status") != "observed":
                continue
            amount = item.get("unit_price_krw_per_meter")
            if isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount) and amount > 0:
                values.append((float(amount), item.get("record_id")))
    if not values:
        for group in fabric_rows:
            stats = group.get("price_per_meter_krw_statistics")
            if not isinstance(stats, dict) or stats.get("status") != "observed":
                continue
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


def _review_evidence(
    *,
    historical_price_analysis: dict[str, Any] | None,
    customer_age_profile: dict[str, Any] | None,
    fabric_price_analysis: tuple[dict[str, Any], ...],
) -> tuple[tuple[dict[str, Any], ...], dict[str, tuple[str, ...]]]:
    """Return source-by-source evidence assessments and IDs grouped by use."""
    evidence: list[dict[str, Any]] = []
    ids_by_kind: dict[str, list[str]] = {
        "price": [], "customer": [], "fabric": [], "fx": [], "benchmark": [],
    }

    def add(source: dict[str, Any], *, kind: str, analysis: dict[str, Any], supports: list[str]) -> str:
        identity_material = json.dumps(
            [source["source_id"], source["source_type"], source["locator"], source.get("version", "")],
            ensure_ascii=False, separators=(",", ":"),
        )
        evidence_id = "source:" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:24]
        if not any(item["evidence_id"] == evidence_id for item in evidence):
            evidence.append({
                "evidence_id": evidence_id,
                "source_id": source["source_id"],
                "source_type": source["source_type"],
                "source_name": source["source_name"],
                "locator": source["locator"],
                "observed_at": source.get("observed_at"),
                "as_of": source.get("as_of"),
                "permission_status": source["permission_status"],
                "supports_fields": supports,
                "analysis": analysis,
            })
        if evidence_id not in ids_by_kind[kind]:
            ids_by_kind[kind].append(evidence_id)
        return evidence_id

    for label, kind, analysis, supports in (
        ("H&M historical customer aggregate", "customer", customer_age_profile,
         ["result.customer_segment_analysis", "result.target_audience_analysis.age_cohort"]),
        ("H&M historical transaction price aggregate", "price", historical_price_analysis,
         ["result.category_price_comparison", "result.price_analysis"]),
    ):
        if not analysis:
            continue
        source_id = str(analysis.get("source_id") or "hm-kaggle-personalized-fashion-recommendations")
        source_type = str(analysis.get("source_type") or "historical_hm_research_aggregate")
        permission = str(analysis.get("permission_status") or "non_commercial_research_only")
        period = analysis.get("period")
        as_of = period.get("end") if isinstance(period, dict) else None
        refs = analysis.get("sources", [])
        if not isinstance(refs, list):
            continue
        if kind == "customer":
            observed = {
                "target_age_cohort": analysis.get("target_age_cohort"),
                "age_bands": analysis.get("age_bands", []),
                "unique_customer_denominator": analysis.get("unique_customer_denominator"),
                "period": period,
                "requested_gender": analysis.get("requested_gender"),
                "gender_filter_applied": analysis.get("gender_filter_applied"),
                "gender_fallback_applied": analysis.get("gender_fallback_applied"),
                "gender_mapping": analysis.get("gender_mapping"),
            }
            interpretation = "Aggregated age-band counts only; this is historical research and not a current customer profile."
            evidence_supports = supports
        else:
            observed = {
                "sample_count": analysis.get("sample_count"),
                "price_statistics": analysis.get("price_statistics"),
                "scaled_price_statistics": analysis.get("scaled_price_statistics"),
                "period": period,
                "requested_product_type": analysis.get("requested_product_type_context"),
                "product_type_filter": analysis.get("product_type_filter"),
                "product_type_filter_applied": analysis.get("product_type_filter_applied"),
                "product_type_fallback_applied": analysis.get("product_type_fallback_applied"),
                "product_type_mapping": (analysis.get("category_mapping") or {}).get("product_type_mapping"),
                "requested_gender": analysis.get("requested_gender"),
                "gender_filter_applied": analysis.get("gender_filter_applied"),
                "gender_fallback_applied": analysis.get("gender_fallback_applied"),
                "category_mapping": analysis.get("category_mapping"),
                "gender_mapping": analysis.get("gender_mapping"),
                "season_filter": analysis.get("season_filter"),
                "season_filter_status": analysis.get("season_filter_status"),
            }
            interpretation = "Historical H&M transaction-price aggregate; the ×590 USD scale is a stated research assumption, not official currency metadata."
            evidence_supports = supports + ["result.margin_estimate", "result.margin_analysis"]
            if analysis.get("usd_krw_conversion"):
                evidence_supports.extend(["result.margin_price_range_analysis"])
        for ref in refs:
            if isinstance(ref, dict) and isinstance(ref.get("object_key"), str):
                add({
                    "source_id": source_id, "source_type": source_type,
                    "source_name": label, "locator": ref["object_key"],
                    "version": str(ref.get("version") or ""), "as_of": as_of,
                    "permission_status": permission,
                }, kind=kind, analysis={"observed": observed, "interpretation": interpretation}, supports=evidence_supports)

    for group in fabric_price_analysis:
        ref = group.get("source_object")
        if isinstance(ref, dict) and isinstance(ref.get("object_key"), str):
            observed_at = max(
                (str(item.get("observed_at")) for item in group.get("sources", [])
                 if isinstance(item, dict) and item.get("observed_at")),
                default=None,
            )
            source_type = str(group.get("source_type") or "owner_provided_public_fabric_listing_snapshot")
            source_rows = group.get("sources", [])
            add({
                "source_id": source_type, "source_type": source_type,
                "source_name": "Owner-provided public fabric listing snapshot",
                "locator": ref["object_key"],
                "version": str(ref.get("version") or ref.get("sha256") or ""),
                "as_of": observed_at, "observed_at": observed_at,
                "permission_status": str(group.get("permission_status") or "unknown"),
            }, kind="fabric", analysis={
                "observed": {
                    "material_query": group.get("material_query"),
                    "sample_count": group.get("sample_count"),
                    "listed_price_statistics": group.get("listed_price_statistics"),
                    "price_per_meter_krw_statistics": group.get("price_per_meter_krw_statistics"),
                    "matched_record_ids": [item.get("record_id") for item in source_rows
                                            if isinstance(item, dict) and item.get("record_id")],
                },
                "interpretation": "Public listing reference only; it is not a verified purchase or production cost, and material composition is not inferred.",
            }, supports=["result.material_cost_analysis", "result.fabric_cost_analysis",
                         "result.margin_estimate", "result.margin_analysis",
                         "result.margin_price_range_analysis"])

    if historical_price_analysis:
        conversion = historical_price_analysis.get("usd_krw_conversion")
        if isinstance(conversion, dict) and conversion.get("rate") is not None:
            add({
                "source_id": str(conversion.get("provider") or "frankfurter"),
                "source_type": "usd_krw_reference_rate",
                "source_name": str(conversion.get("provider") or "Frankfurter USD/KRW reference rate"),
                "locator": "https://api.frankfurter.dev/v2/rate/usd/krw",
                "version": str(conversion.get("rate_date") or ""),
                "as_of": conversion.get("rate_date"),
                "permission_status": "public_reference_data",
            }, kind="fx", analysis={
                "observed": {key: conversion.get(key) for key in
                             ("base_currency", "quote_currency", "rate", "rate_date", "rate_type", "fallback_used", "stale")},
                "interpretation": "Reference conversion rate used to express the historical research scale in KRW; not a price observation.",
            }, supports=["result.price_analysis", "result.margin_estimate", "result.margin_analysis", "result.margin_price_range_analysis"])

    add({
        "source_id": "nyu-stern-damodaran-us-apparel-margin-2026-01",
        "source_type": "industry_gross_margin_benchmark",
        "source_name": "NYU Stern Damodaran Margins by Sector (US), January 2026",
        "locator": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html",
        "version": "2026-01",
        "as_of": "2026-01",
        "permission_status": "public_reference_data",
    }, kind="benchmark", analysis={
        "observed": {"industry": "Apparel", "gross_margin_rate": 0.5688, "firm_count": 35, "region": "US", "period": "January 2026"},
        "interpretation": "Sector-level benchmark scenario, not this product's observed margin or a Korea-specific forecast.",
    }, supports=["result.margin_estimate", "result.margin_analysis", "result.margin_price_range_analysis"])

    return tuple(evidence), {key: tuple(value) for key, value in ids_by_kind.items()}


@dataclass(frozen=True, slots=True)
class SalesMarketReview:
    """Result payload inside the review response envelope."""

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
    evidence_ids_by_kind: dict[str, tuple[str, ...]]
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
                    row.get("verified_price_per_meter_available") is True
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
            "basis": self.margin_estimate.get("basis"),
            "formula": (
                self.margin_estimate.get("formula", {}).get("gross_margin_rate")
                if isinstance(self.margin_estimate.get("formula"), dict)
                else self.margin_estimate.get("formula")
            ) or (
                "estimated_gross_margin_rate = NYU Stern US apparel gross margin benchmark"
                if self.margin_estimate.get("status") in {"benchmark_estimate", "benchmark_only"}
                else None
            ),
            "selling_price": self.margin_estimate.get("selling_price"),
            "currency": self.margin_estimate.get("currency"),
            "margin_rate_percent": (
                round(self.margin_estimate["benchmark_gross_margin_rate"] * 100, 2)
                if self.margin_estimate.get("status") in {"benchmark_estimate", "benchmark_only"}
                and isinstance(self.margin_estimate.get("benchmark_gross_margin_rate"), (int, float)) else None
            ),
            "estimate_type": self.margin_estimate.get("estimate_type"),
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

        ids_by_kind = self.evidence_ids_by_kind

        def evidence_ids(*kinds: str) -> list[str]:
            return list(dict.fromkeys(item for kind in kinds for item in ids_by_kind.get(kind, ())))

        price_rows = [dict(row, evidence_ids=evidence_ids("price")) for row in self.category_price_comparison]
        fabric_rows = [dict(row, evidence_ids=evidence_ids("fabric")) for row in self.material_cost_analysis]
        customer_rows = [dict(row, evidence_ids=evidence_ids("customer")) for row in self.customer_segment_analysis]
        target_kinds = (
            ("customer", "price", "fabric") if self.target_audience_analysis.get("proposal")
            else ("customer",) if self.target_audience_analysis.get("age_cohort") is not None
            else ()
        )
        target_ids = evidence_ids(*target_kinds)
        margin_kinds = ["benchmark"]
        if self.margin_estimate.get("selling_price") is not None:
            margin_kinds.extend(["price", "fx"])
        if self.margin_estimate.get("fabric_unit_cost") is not None:
            margin_kinds.append("fabric")
        margin_ids = evidence_ids(*margin_kinds)
        price_ids = evidence_ids("price", *(('fx',) if proposed_price is not None else ()))
        fabric_ids = evidence_ids("fabric")
        range_ids = evidence_ids("price", "fx", "fabric", "benchmark") if range_margin_analysis is not None else []

        evidence_supports: dict[str, list[str]] = {}
        for path, ids in (
            ("result.category_price_comparison", evidence_ids("price")),
            ("result.price_analysis", price_ids),
            ("result.material_cost_analysis", fabric_ids),
            ("result.fabric_cost_analysis", fabric_ids),
            ("result.customer_segment_analysis", evidence_ids("customer")),
            ("result.target_audience_analysis", target_ids),
            ("result.margin_estimate", margin_ids),
            ("result.margin_analysis", margin_ids),
            ("result.margin_price_range_analysis", range_ids),
        ):
            for evidence_id in ids:
                evidence_supports.setdefault(evidence_id, []).append(path)
        evidence_rows = [
            {**item, "supports_fields": evidence_supports.get(item["evidence_id"], [])}
            for item in self.evidence
        ]
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "draft_id": self.draft_id,
            "producer_id": self.producer_id,
            "status": self.status,
            "draft_context": self.draft_context,
            "category_price_comparison": price_rows,
            "material_cost_analysis": fabric_rows,
            "customer_segment_analysis": customer_rows,
            "target_audience_analysis": {**self.target_audience_analysis, "evidence_ids": target_ids},
            "margin_estimate": {**self.margin_estimate, "evidence_ids": evidence_ids(
                "benchmark",
                *(('price', 'fx') if self.margin_estimate.get("selling_price") is not None else ()),
                *(('fabric',) if self.margin_estimate.get("fabric_unit_cost") is not None else ()),
            )},
            "evidence": evidence_rows,
            "missing_data": list(self.missing_data),
            "errors": list(self.errors),
            "limitations": list(self.limitations),
            "decision_support": self.decision_support,
            "price_analysis": {**price_analysis, "evidence_ids": evidence_ids("price", *(('fx',) if proposed_price is not None else ()))},
            "fabric_cost_analysis": {**fabric_cost_analysis, "evidence_ids": evidence_ids("fabric")},
            "margin_analysis": {**margin_analysis, "evidence_ids": evidence_ids(
                "benchmark",
                *(('price', 'fx') if self.margin_estimate.get("selling_price") is not None else ()),
                *(('fabric',) if self.margin_estimate.get("fabric_unit_cost") is not None else ()),
            )},
            "margin_price_range_analysis": ({
                **range_margin_analysis, "evidence_ids": evidence_ids("price", "fx", "fabric", "benchmark"),
            } if range_margin_analysis is not None else None),
            "target_audience": {**self.target_audience_analysis, "evidence_ids": target_ids},
        }


def _product_context(product: dict[str, Any]) -> dict[str, Any]:
    """Keep only the allowlisted product attributes in the review result."""
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
    return context


def build_sales_market_review(
    request_id: str,
    *,
    product_context: dict[str, Any],
    producer_id: str = "sales-market-agent",
    fabric_price_analysis: tuple[dict[str, Any], ...] = (),
    fabric_price_error: str | None = None,
    fabric_price_source_status: str | None = None,
    historical_price_source_status: str | None = None,
    historical_price_analysis: dict[str, Any] | None = None,
    historical_price_error: str | None = None,
    customer_age_profile: dict[str, Any] | None = None,
    customer_age_error: str | None = None,
    customer_age_source_status: str | None = None,
    target_audience_proposal: dict[str, Any] | None = None,
) -> SalesMarketReview:
    """Assemble available source analyses into the review result.

    Product attributes are restricted to the five-field request projection.
    Fabric and historical H&M price analyses are supplied by read-only R2 sources.
    """
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string.")
    if not isinstance(product_context, dict):
        raise TypeError("product_context must be an object.")

    safe_product_context = _product_context(product_context)
    product = safe_product_context
    errors: list[dict[str, Any]] = []
    if customer_age_error:
        errors.append({"code": "CUSTOMER_AGE_RESEARCH_FAILED", "message": customer_age_error})
    if historical_price_error:
        errors.append({"code": "HM_HISTORICAL_PRICE_RESEARCH_FAILED", "message": historical_price_error})
    if fabric_price_error:
        errors.append({"code": "R2_FABRIC_PRICE_READ_FAILED", "message": fabric_price_error})
    missing: list[dict[str, Any]] = []
    fabrics = list(fabric_price_analysis)
    if historical_price_analysis is None:
        historical_reason = (
            "source_error" if historical_price_error or historical_price_source_status == "error" else
            "no_match" if historical_price_source_status == "no_match" else
            "source_not_configured" if historical_price_source_status == "not_configured" else
            "source_not_configured_or_disabled"
        )
        missing.append({"field": "historical_hm_transaction_prices",
                        "reason": historical_reason,
                        "message": historical_price_error or (
                            "No historical price observations matched the requested product." if historical_reason == "no_match"
                            else "H&M non-commercial research is disabled." if historical_reason == "source_not_configured_or_disabled"
                            else "Enable non-commercial H&M research mode and configure its R2 source."
                        )})
    if not fabrics:
        reason = (
            "source_error" if fabric_price_error else
            "material_not_specified" if fabric_price_source_status == "not_requested" or not product.get("material") else
            "source_not_configured" if fabric_price_source_status == "not_configured" else
            "no_match" if fabric_price_source_status == "no_match" else
            "source_not_configured_or_no_match"
        )
        if fabric_price_error:
            message = fabric_price_error
        elif reason == "material_not_specified":
            message = "Material was not supplied, so fabric prices were not queried."
        elif fabric_price_source_status == "not_configured":
            message = "R2 fabric price source is not configured."
        elif fabric_price_source_status == "no_match":
            message = "No fabric listing matched the supplied material."
        else:
            message = "No R2 fabric listing prices matched the supplied material; per-meter cost requires a verified sales unit."
        missing.append({"field": "fabric_costs", "reason": reason,
                        "message": message})
    if customer_age_profile is None:
        age_reason = (
            "source_error" if customer_age_error else
            "no_match" if customer_age_source_status == "no_match" else
            "source_not_configured" if customer_age_source_status == "not_configured" else
            "source_not_configured_or_disabled"
        )
        missing.append({"field": "customer_segments",
                        "reason": age_reason,
                        "message": customer_age_error or (
                            "No customer age profile matched the requested product." if age_reason == "no_match"
                            else "H&M non-commercial research is disabled." if age_reason == "source_not_configured_or_disabled"
                            else "Enable non-commercial H&M research mode and configure its R2 source."
                        )})
    conversion = historical_price_analysis.get("usd_krw_conversion", {}) if historical_price_analysis else {}
    margin_price = conversion.get("suggested_price_krw")
    margin_currency = "KRW" if margin_price is not None else None
    fabric_unit_cost, fabric_unit, fabric_record_ids = _fabric_reference(tuple(fabrics))
    margin = calculate_benchmark_margin_estimate(
        selling_price=margin_price, currency=margin_currency,
        fabric_unit_cost=fabric_unit_cost, fabric_unit=fabric_unit,
    )
    if fabric_record_ids:
        margin["fabric_reference_record_ids"] = fabric_record_ids
    if margin_price is None:
        margin["status"] = "benchmark_only"
    limitations: list[str] = []
    material_rows = tuple(fabrics)
    if margin.get("status") in {"benchmark_estimate", "benchmark_only"}:
        limitations.append("[마진 참고] 미국 의류 업계 평균 매출총이익률 벤치마크이며 이 상품의 실제 마진이 아닙니다.")
    if historical_price_analysis is not None:
        limitations.append("[거래 가격 자료] H&M Kaggle 거래 가격은 과거 비상업 연구 참고치이며 현재 시장 판매가가 아닙니다.")
    if fabrics:
        limitations.append("[원단 자료] R2 공개 목록 가격은 참고치이며 검증된 구매 단가나 제품 생산 원가가 아닙니다.")
    if customer_age_profile is not None:
        limitations.append("[고객 연령 자료] H&M 과거 비상업 집계는 현재 고객 구성이나 수요를 나타내지 않습니다.")
    target_audience_status = (
        "ok" if target_audience_proposal is not None
        else "partial" if customer_age_profile is not None
        else "no_results"
    )
    has_review_content = bool(
        historical_price_analysis or fabrics
        or customer_age_profile or target_audience_proposal
    )
    historical_status = historical_price_source_status or (
        "available" if historical_price_analysis is not None else
        "error" if historical_price_error else "disabled"
    )
    age_status = customer_age_source_status or (
        "available" if customer_age_profile is not None else
        "error" if customer_age_error else "disabled"
    )
    source_statuses = [historical_status, age_status]
    if product.get("material"):
        fabric_status = fabric_price_source_status or (
            "available" if fabrics else "error" if fabric_price_error else "not_configured"
        )
        source_statuses.append(fabric_status)
    research_is_incomplete = any(status != "available" for status in source_statuses)
    if not has_review_content:
        # A sector benchmark is context, not product or market evidence. Do not
        # report partial merely because the benchmark calculator returned it.
        review_status = "no_results"
    elif errors or research_is_incomplete:
        review_status = "partial"
    else:
        review_status = "ok"
    review_evidence, evidence_ids_by_kind = _review_evidence(
        historical_price_analysis=historical_price_analysis,
        customer_age_profile=customer_age_profile,
        fabric_price_analysis=tuple(fabrics),
    )
    return SalesMarketReview(
        schema_version=REVIEW_SCHEMA_VERSION,
        request_id=request_id,
        draft_id=None,
        producer_id=producer_id,
        status=review_status,
        draft_context=safe_product_context,
        category_price_comparison=(historical_price_analysis,) if historical_price_analysis else (),
        material_cost_analysis=material_rows,
        customer_segment_analysis=(
            (customer_age_profile,) if customer_age_profile is not None else ()
        ),
        target_audience_analysis={
            "status": target_audience_status,
            "proposal": target_audience_proposal,
            "age_cohort": customer_age_profile.get("target_age_cohort") if customer_age_profile else None,
            "limitations": list(dict.fromkeys(
                (["Age profile is unavailable or disabled."] if customer_age_profile is None else [])
                + (["LLM target hypothesis is unavailable or not authorized."] if target_audience_proposal is None else [])
            )),
        },
        margin_estimate=margin,
        evidence=review_evidence,
        evidence_ids_by_kind=evidence_ids_by_kind,
        missing_data=tuple(missing),
        errors=tuple(errors),
        limitations=tuple(dict.fromkeys(limitations)),
    )
