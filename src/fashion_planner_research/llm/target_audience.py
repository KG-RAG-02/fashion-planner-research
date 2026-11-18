"""Opt-in text-only target-audience hypothesis generation."""

from __future__ import annotations

import json
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from typing import Any, Callable

from ..resource_paths import PROMPTS_DIR
from .responses_api import LlmCallError, OpenAIResponsesClient


_SYSTEM = (PROMPTS_DIR / "target_audience_hypothesis" / "system.md").read_text(encoding="utf-8")
_OUTPUT_FIELDS = {
    "proposal", "primary_segment", "secondary_segments", "age_evidence", "product_fit",
    "merchandising_actions", "channel_content_hypotheses",
    "validation_plan", "evidence_basis", "limitations",
}
_LOGGER = logging.getLogger(__name__)
_LLM_CACHE_TTL_SECONDS = 300
_LLM_CACHE_MAX_ENTRIES = 64
_LLM_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_LLM_CACHE_INFLIGHT: dict[str, Future[dict[str, Any]]] = {}
_LLM_CACHE_LOCK = threading.Lock()


def _cached_llm_result(
    client: OpenAIResponsesClient, *, instructions: str, input_text: str,
    producer: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    prompt_digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
    identity = {
        "prompt_digest": prompt_digest,
        "input_text": input_text,
        "model": client.model,
        "credential_fingerprint": hashlib.sha256(client.api_key.encode("utf-8")).hexdigest(),
        "max_output_tokens": client.max_output_tokens,
    }
    cache_key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _LLM_CACHE_LOCK:
        expired = [key for key, (expires, _value) in _LLM_CACHE.items() if expires <= now]
        for key in expired:
            _LLM_CACHE.pop(key, None)
        cached = _LLM_CACHE.get(cache_key)
        if cached is not None:
            _LLM_CACHE.move_to_end(cache_key)
            _LOGGER.info("Target LLM exact-input cache hit")
            return deepcopy(cached[1])
        pending = _LLM_CACHE_INFLIGHT.get(cache_key)
        if pending is None:
            pending = Future()
            _LLM_CACHE_INFLIGHT[cache_key] = pending
            owner = True
        else:
            owner = False
    if not owner:
        _LOGGER.info("Target LLM joined identical in-flight request")
        return deepcopy(pending.result())
    try:
        started = time.perf_counter()
        value = producer()
        _LOGGER.info("Target LLM call and output validation completed in %.3fs input_bytes=%d",
                     time.perf_counter() - started, len(input_text.encode("utf-8")))
        with _LLM_CACHE_LOCK:
            _LLM_CACHE[cache_key] = (time.monotonic() + _LLM_CACHE_TTL_SECONDS, deepcopy(value))
            _LLM_CACHE.move_to_end(cache_key)
            while len(_LLM_CACHE) > _LLM_CACHE_MAX_ENTRIES:
                _LLM_CACHE.popitem(last=False)
        pending.set_result(deepcopy(value))
        return value
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _LLM_CACHE_LOCK:
            _LLM_CACHE_INFLIGHT.pop(cache_key, None)


def _nonempty_string(value: Any, *, maximum: int = 1000) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _string_list(value: Any, *, minimum: int, maximum: int, item_maximum: int = 500) -> bool:
    return (
        isinstance(value, list) and minimum <= len(value) <= maximum
        and all(_nonempty_string(item, maximum=item_maximum) for item in value)
    )


def propose_target_audience(
    *, product_context: dict[str, Any], customer_profile: dict[str, Any] | None,
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
            "aggregation", "limitations",
        )
        age_profile = {key: customer_profile[key] for key in allowed_age_fields if key in customer_profile}
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
            "limitations": group.get("limitations", []),
        })
    payload = {
        "product_context": {key: product_context.get(key) for key in
                            ("category", "product_type", "season", "material", "gender")},
        "historical_customer_aggregate": age_profile,
        "fabric_listing_reference": fabric_references,
    }
    text = "Analyze this JSON fashion-product planning data using the system instructions and return the required JSON object:\n" + json.dumps(
        payload, ensure_ascii=False, allow_nan=False,
    )
    def finish(response: dict[str, Any]) -> dict[str, Any]:
        output = response.get("output") if isinstance(response, dict) else None
        if not isinstance(output, dict) or set(output) != _OUTPUT_FIELDS:
            raise LlmCallError("Target audience output did not match the expected fields.")
        if not all(_nonempty_string(output.get(key), maximum=1200) for key in ("proposal", "primary_segment", "age_evidence")):
            raise LlmCallError("Target audience summary fields must be non-empty strings.")
        secondary_segments = output.get("secondary_segments")
        if isinstance(secondary_segments, str):
            secondary_segments = [secondary_segments]
        if isinstance(secondary_segments, list):
            secondary_segments = [
                item.strip() for item in secondary_segments
                if isinstance(item, str) and item.strip()
            ][:2]
        else:
            secondary_segments = []
        if not secondary_segments or any(len(item) > 500 for item in secondary_segments):
            secondary_segments = ["추가 자료 전에는 보조 타깃을 특정하지 않음"]
        output["secondary_segments"] = secondary_segments
        product_fit = output.get("product_fit")
        if (not isinstance(product_fit, dict)
                or set(product_fit) != {"style_direction", "wearing_occasions", "needs_hypotheses", "styling_cues"}
                or not _nonempty_string(product_fit.get("style_direction"))
                or any(not _string_list(product_fit.get(key), minimum=2, maximum=4)
                       for key in ("wearing_occasions", "needs_hypotheses", "styling_cues"))):
            raise LlmCallError("Target audience product_fit fields are invalid.")
        for key, minimum, maximum in (
            ("merchandising_actions", 2, 4), ("channel_content_hypotheses", 2, 4),
            ("validation_plan", 3, 5), ("evidence_basis", 2, 6), ("limitations", 2, 6),
        ):
            if not _string_list(output.get(key), minimum=minimum, maximum=maximum):
                raise LlmCallError(f"Target audience {key} must contain {minimum}–{maximum} strings.")
        output["type"] = "hypothesis"
        output["provider"] = "openai_responses_api" if call is None else "injected_test_client"
        evidence_ids: list[str] = []
        for source in (customer_profile,):
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

    if call is not None:
        return finish(call(instructions=_SYSTEM, input_text=text))
    actual_client = client or OpenAIResponsesClient()
    return _cached_llm_result(
        actual_client, instructions=_SYSTEM, input_text=text,
        producer=lambda: finish(actual_client.create_json_response(instructions=_SYSTEM, input_text=text)),
    )
