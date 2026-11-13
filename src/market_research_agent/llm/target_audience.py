"""Opt-in text-only target-audience hypothesis generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .responses_api import LlmCallError, OpenAIResponsesClient


_ROOT = Path(__file__).resolve().parents[3]
_SYSTEM = (_ROOT / "prompts" / "target_audience_hypothesis" / "v1" / "rendered_system.md").read_text(encoding="utf-8")
_OUTPUT_FIELDS = {
    "proposal", "primary_segment", "secondary_segments", "age_evidence", "product_fit",
    "price_positioning", "merchandising_actions", "channel_content_hypotheses",
    "validation_plan", "evidence_basis", "limitations",
}


def _nonempty_string(value: Any, *, maximum: int = 1000) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _string_list(value: Any, *, minimum: int, maximum: int, item_maximum: int = 500) -> bool:
    return (
        isinstance(value, list) and minimum <= len(value) <= maximum
        and all(_nonempty_string(item, maximum=item_maximum) for item in value)
    )


def propose_target_audience(
    *, product_context: dict[str, Any], customer_profile: dict[str, Any] | None,
    market_price_reference: dict[str, Any] | None = None,
    fabric_listing_reference: list[dict[str, Any]] | None = None,
    client: OpenAIResponsesClient | None = None,
    call: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    age_profile = None
    if isinstance(customer_profile, dict):
        allowed_age_fields = (
            "source_id", "source_type", "permission_status", "category",
            "product_merchandise_segment", "unique_customer_denominator",
            "target_age_cohort", "age_bands", "period", "age_semantics",
            "aggregation", "limitations", "sources",
        )
        age_profile = {key: customer_profile[key] for key in allowed_age_fields if key in customer_profile}
    price_reference = None
    if isinstance(market_price_reference, dict):
        price_reference = {
            key: market_price_reference.get(key) for key in (
                "source_id", "source_type", "category", "mapped_product_type_category",
                "merchandise_segment", "sample_count", "article_count", "period",
                "price_statistics", "scaled_price_statistics", "usd_krw_conversion",
                "category_mapping", "limitations", "sources",
            ) if key in market_price_reference
        }
    fabric_references = []
    for group in fabric_listing_reference or []:
        if not isinstance(group, dict):
            continue
        fabric_references.append({
            "material_query": group.get("material_query"),
            "source_category": group.get("source_category"),
            "source_type": group.get("source_type"),
            "sample_count": group.get("sample_count"),
            "price_per_meter_krw_statistics": group.get("price_per_meter_krw_statistics"),
            "source_record_ids": [
                item.get("record_id") for item in group.get("sources", [])
                if isinstance(item, dict) and isinstance(item.get("record_id"), str)
            ],
            "limitations": group.get("limitations", []),
        })
    payload = {
        "product_context": {key: product_context.get(key) for key in
                            ("category", "product_type", "season", "material", "gender",
                             "proposed_price", "currency")},
        "historical_customer_aggregate": age_profile,
        "market_price_reference": price_reference,
        "fabric_listing_reference": fabric_references,
    }
    text = "Analyze this JSON fashion-product planning data using the system instructions and return the required JSON object:\n" + json.dumps(
        payload, ensure_ascii=False, allow_nan=False,
    )
    response = call(instructions=_SYSTEM, input_text=text) if call else (
        client or OpenAIResponsesClient()
    ).create_json_response(instructions=_SYSTEM, input_text=text)
    output = response.get("output") if isinstance(response, dict) else None
    if not isinstance(output, dict) or set(output) != _OUTPUT_FIELDS:
        raise LlmCallError("Target audience output did not match the expected fields.")
    if not all(_nonempty_string(output.get(key), maximum=1200) for key in ("proposal", "primary_segment", "age_evidence")):
        raise LlmCallError("Target audience summary fields must be non-empty strings.")
    if not _string_list(output.get("secondary_segments"), minimum=1, maximum=2):
        raise LlmCallError("Target audience secondary_segments must contain one or two strings.")
    product_fit = output.get("product_fit")
    if (not isinstance(product_fit, dict)
            or set(product_fit) != {"style_direction", "wearing_occasions", "needs_hypotheses", "styling_cues"}
            or not _nonempty_string(product_fit.get("style_direction"))
            or any(not _string_list(product_fit.get(key), minimum=2, maximum=4)
                   for key in ("wearing_occasions", "needs_hypotheses", "styling_cues"))):
        raise LlmCallError("Target audience product_fit fields are invalid.")
    price_positioning = output.get("price_positioning")
    position = price_positioning.get("position") if isinstance(price_positioning, dict) else None
    confidence = price_positioning.get("confidence") if isinstance(price_positioning, dict) else None
    if (not isinstance(price_positioning, dict)
            or set(price_positioning) != {"position", "rationale", "confidence"}
            or not isinstance(position, str)
            or position not in {"value", "mid_market", "premium_hypothesis", "undetermined"}
            or not isinstance(confidence, str)
            or confidence not in {"low", "medium", "high"}
            or not _nonempty_string(price_positioning.get("rationale"))):
        raise LlmCallError("Target audience price_positioning fields are invalid.")
    for key, minimum, maximum in (
        ("merchandising_actions", 2, 4), ("channel_content_hypotheses", 2, 4),
        ("validation_plan", 3, 5), ("evidence_basis", 2, 6), ("limitations", 2, 6),
    ):
        if not _string_list(output.get(key), minimum=minimum, maximum=maximum):
            raise LlmCallError(f"Target audience {key} must contain {minimum}–{maximum} strings.")
    output["type"] = "hypothesis"
    output["provider"] = "openai_responses_api" if call is None else "injected_test_client"
    evidence_ids: list[str] = []
    for source in (customer_profile, market_price_reference):
        if isinstance(source, dict):
            source_id = source.get("source_id")
            if isinstance(source_id, str) and source_id and source_id not in evidence_ids:
                evidence_ids.append(source_id)
    for group in fabric_listing_reference or []:
        if not isinstance(group, dict):
            continue
        record_ids = group.get("source_record_ids", [])
        if not record_ids:
            record_ids = [item.get("record_id") for item in group.get("sources", [])
                          if isinstance(item, dict)]
        for record_id in record_ids:
            if isinstance(record_id, str) and record_id and record_id not in evidence_ids:
                evidence_ids.append(record_id)
    output["evidence_ids"] = evidence_ids
    return output
