"""Authenticated HTTP adapter for the P07 0.3 sales-market review contract."""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .domain.currency_conversion import CurrencyConversionError, convert_usd_to_krw, fetch_usd_krw_reference_rate
from .domain.sales_market_review import REVIEW_SCHEMA_VERSION, build_sales_market_review
from .llm.target_audience import propose_target_audience
from .llm.responses_api import LlmCallError, OpenAIResponsesClient
from .runtime_settings import hm_research_noncommercial_enabled, target_llm_transfer_authorized
from .sources.sales.hm_kaggle.hm_combined_research_source import HmCombinedResearchSource

REVIEW_CONTRACT_VERSION = "vogue-sales-market-review/0.3-proposal"
REVIEW_PATH = "/v1/agents/sales-market-agent/reviews"
AGENT_ID = "sales-market-agent"
MAX_BODY_BYTES = 1_048_576
_LOGGER = logging.getLogger(__name__)
_REVIEW_REQUIRED_FIELDS = frozenset({
    "schema_version", "request_id", "draft_id", "producer_id", "status", "draft_context",
    "category_price_comparison", "material_cost_analysis", "customer_segment_analysis",
    "target_audience_analysis", "margin_estimate", "evidence", "missing_data", "errors",
    "limitations", "decision_support", "price_analysis", "fabric_cost_analysis",
    "margin_analysis", "margin_price_range_analysis", "target_audience",
})
def _safe_error_request_id(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(char.isalnum() or char in "-_.:" for char in value)
    ):
        return value
    return None

class RequestValidationError(ValueError):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status

def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")

def _validate_json_tree(value: Any, field: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise RequestValidationError("INVALID_INPUT", f"{field} must contain valid JSON values.") from None

def _validate_review_result(review: Any, request_id: str) -> None:
    """Reject malformed or status-inconsistent P07 response results."""
    if not isinstance(review, dict) or not _REVIEW_REQUIRED_FIELDS.issubset(review):
        raise ValueError("Review result is missing contract fields.")
    if (
        review.get("schema_version") != REVIEW_SCHEMA_VERSION
        or review.get("request_id") != request_id
        or review.get("producer_id") != AGENT_ID
        or review.get("status") not in {"ok", "partial", "no_results", "error"}
    ):
        raise ValueError("Review result identity or status is invalid.")
    for field in (
        "category_price_comparison", "material_cost_analysis", "customer_segment_analysis",
        "evidence", "missing_data", "errors", "limitations",
    ):
        if not isinstance(review.get(field), list):
            raise ValueError(f"Review result field {field} must be an array.")
    for field in (
        "draft_context", "target_audience_analysis", "margin_estimate", "decision_support",
        "price_analysis", "fabric_cost_analysis", "margin_analysis", "target_audience",
    ):
        if not isinstance(review.get(field), dict):
            raise ValueError(f"Review result field {field} must be an object.")
    decision = review["decision_support"]
    if not isinstance(decision.get("ready_for_human_decision"), bool):
        raise ValueError("Review decision support must declare decision readiness.")
    content_paths = {
        "result.category_price_comparison", "result.material_cost_analysis",
        "result.customer_segment_analysis", "result.target_audience_analysis",
        "result.price_analysis", "result.fabric_cost_analysis",
    }
    substantive_evidence = any(
        isinstance(item, dict)
        and isinstance(item.get("supports_fields"), list)
        and bool(content_paths.intersection(item["supports_fields"]))
        for item in review["evidence"]
    )
    has_content = bool(
        review["category_price_comparison"] or review["material_cost_analysis"]
        or review["customer_segment_analysis"] or substantive_evidence
        or review["target_audience"].get("proposal")
    )
    if review["status"] in {"ok", "partial"} and not has_content:
        raise ValueError("Review status claims results but contains no review content.")
    if review["status"] == "no_results" and has_content:
        raise ValueError("Review status is no_results but review content is present.")
    if decision["ready_for_human_decision"] != (review["status"] in {"ok", "partial"}):
        raise ValueError("Review status and decision readiness disagree.")

def _p08_review_projection(review: dict[str, Any]) -> dict[str, Any]:
    """Keep the P07 response contract while omitting source-row detail P08 does not consume.

    P08 retains this response byte-for-byte and reads the aggregate price, fabric,
    customer, target, margin, evidence-reference, and status fields. The source
    adapters' per-object metadata and per-listing records are not part of that
    consumer surface and made the response several times larger than its summary.
    """
    def pick(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {field: value[field] for field in fields if field in value}

    price_fields = (
        "source_id", "source_type", "permission_status", "operational_eligible",
        "category", "mapped_product_type_category", "product_type_filter",
        "requested_product_type_context", "product_type_filter_applied",
        "product_type_fallback_applied", "requested_season", "season_filter_status",
        "category_mapping", "merchandise_segment", "requested_gender", "gender_mapping",
        "gender_filter_applied", "gender_fallback_applied", "sample_count", "article_count",
        "period", "price_statistics", "scaled_price_statistics", "usd_krw_conversion", "evidence_ids",
    )
    customer_fields = (
        "source_id", "source_type", "permission_status", "operational_eligible", "category",
        "category_mapping", "product_merchandise_segment", "target_age_cohort",
        "unique_customer_denominator", "sample_size_suppressed", "period", "age_semantics",
        "aggregation", "requested_product_type_context", "requested_product_category",
        "requested_product_type", "product_type_mapping", "requested_gender", "gender_mapping",
        "gender_filter_applied", "gender_fallback_applied", "evidence_ids",
    )
    fabric_fields = (
        "material_query", "source_category", "source_type", "source_grade", "sample_count",
        "currency", "original_unit", "listed_price_statistics", "price_per_meter_krw_statistics",
        "estimated_price_per_meter_available",
        "cost_per_meter_operational_eligible", "category_mapping", "permission_status",
        "operational_eligible", "evidence_ids",
    )
    compact = dict(review)
    compact["category_price_comparison"] = [
        pick(row, price_fields) for row in review.get("category_price_comparison", [])
        if isinstance(row, dict)
    ]
    compact["customer_segment_analysis"] = [
        pick(row, customer_fields) for row in review.get("customer_segment_analysis", [])
        if isinstance(row, dict)
    ]
    compact["material_cost_analysis"] = [
        pick(row, fabric_fields) for row in review.get("material_cost_analysis", [])
        if isinstance(row, dict)
    ]
    for row in compact["category_price_comparison"]:
        row["price_statistics"] = pick(row.get("price_statistics"), (
            "mean", "median", "p25", "p75", "currency", "unit", "statistic",
        ))
        row["scaled_price_statistics"] = pick(row.get("scaled_price_statistics"), (
            "mean", "median", "p25", "p75", "scale_factor", "currency", "unit", "method_source",
        ))
        row["usd_krw_conversion"] = pick(row.get("usd_krw_conversion"), (
            "rate", "rate_date", "provider", "rate_type", "fallback_used",
        ))
        row["category_mapping"] = pick(row.get("category_mapping"), (
            "mapped_category", "mapping_id", "mapping_version", "matching", "requested_category",
            "method", "score", "threshold", "requested_product_type", "product_type_mapping",
            "gender_mapping",
        ))
    for row in compact["customer_segment_analysis"]:
        row["category_mapping"] = pick(row.get("category_mapping"), (
            "requested_category", "mapped_category", "method", "score", "threshold",
        ))
    for row in compact["material_cost_analysis"]:
        row["listed_price_statistics"] = pick(row.get("listed_price_statistics"), (
            "minimum", "median", "maximum", "currency",
        ))
        row["price_per_meter_krw_statistics"] = pick(row.get("price_per_meter_krw_statistics"), (
            "sample_count", "minimum", "median", "maximum", "currency", "unit", "status",
            "confidence_counts", "method",
        ))
        row["category_mapping"] = pick(row.get("category_mapping"), (
            "mapping_id", "mapping_version", "matched_source_categories", "matched_terms",
        ))

    # P08 reads these fields from the dedicated summary objects below. The
    # detailed listing rows were duplicated here and in material_cost_analysis.
    compact["fabric_cost_analysis"] = pick(review.get("fabric_cost_analysis"), (
        "status", "unit", "currency", "matched_material", "limitations", "evidence_ids",
    ))
    compact["margin_analysis"] = pick(review.get("margin_analysis"), (
        "status", "basis", "selling_price", "currency", "margin_rate_percent",
        "estimate_type", "benchmark_source", "benchmark_sample_size", "benchmark_region",
        "limitations", "evidence_ids",
    ))
    compact["margin_estimate"] = pick(review.get("margin_estimate"), (
        "status", "estimate_type", "basis", "benchmark_gross_margin_rate",
        "benchmark_source", "benchmark_sample_size", "benchmark_region", "selling_price",
        "currency", "evidence_ids",
    ))
    compact["price_analysis"] = pick(review.get("price_analysis"), (
        "status", "hm_average_price_usd", "raw_normalized_mean", "currency", "source_currency",
        "unit", "scale_factor", "converted_average_krw", "exchange_rate", "sample_size",
        "period", "proposed_price", "proposal_reason", "evidence_ids",
    ))
    compact["margin_price_range_analysis"] = None

    # P08's consumers use target_audience. Keep the duplicate legacy alias as a
    # small status/age summary so response schema keys remain stable.
    target = pick(review.get("target_audience"), (
        "status", "proposal", "age_cohort", "limitations", "evidence_ids",
        "transfer_authorized", "llm_call_status",
    ))
    proposal = target.get("proposal")
    if isinstance(proposal, dict):
        target["proposal"] = pick(proposal, (
            "proposal", "primary_segment", "secondary_segments", "product_fit",
            "channel_content_hypotheses", "merchandising_actions",
        ))
        for field, limit in (("proposal", 360), ("primary_segment", 360)):
            text = target["proposal"].get(field)
            if isinstance(text, str) and len(text) > limit:
                target["proposal"][field] = text[:limit].rsplit(" ", 1)[0].rstrip() + "…"
        for field, limit in (("secondary_segments", 180), ("channel_content_hypotheses", 160),
                             ("merchandising_actions", 160)):
            values = target["proposal"].get(field)
            if isinstance(values, list):
                target["proposal"][field] = [
                    value[:limit].rsplit(" ", 1)[0].rstrip() + "…" if isinstance(value, str) and len(value) > limit else value
                    for value in values[:3]
                ]
        product_fit = target["proposal"].get("product_fit")
        if isinstance(product_fit, dict):
            occasions = product_fit.get("wearing_occasions")
            target["proposal"]["product_fit"] = {
                "wearing_occasions": [
                    value[:120].rsplit(" ", 1)[0].rstrip() + "…" if isinstance(value, str) and len(value) > 120 else value
                    for value in occasions[:3]
                ] if isinstance(occasions, list) else []
            }
    compact["target_audience"] = target
    compact["target_audience_analysis"] = pick(review.get("target_audience_analysis"), (
        "status", "age_cohort", "limitations", "evidence_ids",
    ))

    evidence_rows = []
    for row in review.get("evidence", []):
        if not isinstance(row, dict):
            continue
        item = pick(row, (
            "evidence_id", "source_id", "source_type", "source_name", "locator",
            "observed_at", "as_of", "permission_status", "supports_fields",
        ))
        analysis = row.get("analysis")
        if isinstance(analysis, dict) and isinstance(analysis.get("interpretation"), str):
            item["analysis"] = {"interpretation": analysis["interpretation"]}
        evidence_rows.append(item)
    compact["evidence"] = evidence_rows
    limitations = list(review.get("limitations", []))
    if compact["category_price_comparison"]:
        limitations.append(
            "H&M 가격은 2018–2020년 비상업 연구 집계입니다. ×590 USD는 연구 환산 가정이며 원천 통화 표기가 아닙니다; 시즌 차원은 없습니다."
        )
    if compact["material_cost_analysis"]:
        limitations.append(
            "원단은 공개 listing 참고값이며 미터당 환산은 추정치입니다. 검증된 구매·생산 원가가 아닙니다."
        )
    if compact["customer_segment_analysis"]:
        limitations.append(
            "고객 연령은 과거 집계이며 성별 필터는 고객 성별이 아닌 구매 상품 세그먼트 기준입니다."
        )
    compact["limitations"] = list(dict.fromkeys(limitations))
    return compact

def _review_error_payload(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    return {
        "contract_version": REVIEW_CONTRACT_VERSION,
        "request_id": request_id,
        "error": {"code": code, "message": message},
    }

def context_from_review_request(value: Any) -> tuple[str, dict[str, Any]]:
    """Validate and project the minimal product payload accepted from P06."""
    if not isinstance(value, dict) or set(value) != {"contract_version", "request_id", "input"}:
        raise RequestValidationError("INVALID_ENVELOPE", "Review request envelope is invalid.")
    contract_version = value.get("contract_version")
    if contract_version != REVIEW_CONTRACT_VERSION:
        raise RequestValidationError("UNSUPPORTED_CONTRACT_VERSION", "Unsupported review contract_version.")
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip() or _safe_error_request_id(request_id) != request_id:
        raise RequestValidationError("INVALID_REQUEST_ID", "request_id is invalid.")
    payload = value.get("input")
    if (not isinstance(payload, dict)
            or set(payload) - {"request_id", "product", "product_evidence"}
            or not {"request_id", "product", "product_evidence"}.issubset(payload)):
        raise RequestValidationError("INVALID_INPUT", "Review input contains unsupported fields.")
    if payload.get("request_id") != request_id:
        raise RequestValidationError("REQUEST_ID_MISMATCH", "Envelope and input request_id must match.")
    product = payload.get("product")
    allowed_product_fields = {"category", "product_type", "gender", "season", "material"}
    if not isinstance(product, dict) or set(product) - allowed_product_fields:
        raise RequestValidationError("INVALID_PRODUCT", "input.product contains unsupported fields.")
    category = product.get("category")
    if not isinstance(category, str) or not category.strip() or len(category) > 120:
        raise RequestValidationError("INVALID_PRODUCT", "input.product.category is required.")
    for field in ("product_type", "gender", "season", "material"):
        item = product.get(field)
        if item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 120):
            raise RequestValidationError("INVALID_PRODUCT", f"input.product.{field} is invalid.")
    gender = product.get("gender")
    if gender is not None and gender not in {"women", "men", "unisex", "unknown"}:
        raise RequestValidationError("INVALID_GENDER", "input.product.gender must be women, men, unisex, or unknown.")
    field_evidence = payload.get("product_evidence")
    if not isinstance(field_evidence, dict) or set(field_evidence) != set(product):
        raise RequestValidationError("INVALID_PRODUCT_EVIDENCE", "input.product_evidence must describe every supplied product field.")
    allowed_kinds = {"user_requested", "evidence_observation", "proposal", "hypothesis", "to_validate", "unknown"}
    for name, metadata in field_evidence.items():
        if (not isinstance(metadata, dict) or set(metadata) != {"kind", "source_ref", "evidence_ids"}
                or metadata.get("kind") not in allowed_kinds
                or not isinstance(metadata.get("source_ref"), str) or not metadata["source_ref"].strip()
                or len(metadata["source_ref"]) > 200
                or not isinstance(metadata.get("evidence_ids"), list)
                or len(metadata["evidence_ids"]) > 100
                or any(not isinstance(item, str) or not item.strip() or len(item) > 256 for item in metadata["evidence_ids"])):
            raise RequestValidationError("INVALID_PRODUCT_EVIDENCE", f"input.product_evidence.{name} is invalid.")
    _validate_json_tree(product, "input.product")
    return request_id, product

def _load_project_r2_env() -> None:
    """Load storage credentials, service auth, and provider settings from local .env."""
    if os.getenv("VOGUE_PROJECT07_DISABLE_DOTENV", "").casefold() in {"1", "true", "yes"}:
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not (key.startswith("R2_") or (key.startswith("VOGUE_HM_")
                                 and key != "VOGUE_HM_RESEARCH_NONCOMMERCIAL")
                                 or key == "VOGUE_AGENT_SERVICE_BEARER_TOKEN"
                                 or key.startswith("PROJECT07_HM_R2_")
                                 or key == "LLM_API_KEY"
                                 or key == "LLM_MODEL"
                                 ):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)

def make_handler(*, service_mode: bool = False):
    """Build the HTTP handler for the deployed P08 0.3 review contract."""
    class Project07Handler(BaseHTTPRequestHandler):
        server_version = "Project07Review/0.3"

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _send_json(self, status: int, body: dict[str, Any], *, headers=None) -> None:
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                if getattr(self, "command", None) != "HEAD":
                    self.wfile.write(encoded)
            except (ConnectionError, OSError):
                _LOGGER.info("P07 response delivery interrupted before completion")

        def _method_not_allowed(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if path == REVIEW_PATH:
                self._send_json(405, _review_error_payload("METHOD_NOT_ALLOWED", "This route accepts POST."),
                                headers={"Allow": "POST"})
            else:
                self.send_error(501, "Unsupported method (%r)" % self.command)

        do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _method_not_allowed

        def _authorized(self) -> bool:
            expected = os.environ.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "")
            authorization = self.headers.get("Authorization", "")
            supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
            return bool(expected and supplied) and hmac.compare_digest(supplied.encode(), expected.encode())

        def do_GET(self) -> None:
            if unquote(urlsplit(self.path).path) == REVIEW_PATH:
                self._method_not_allowed()
            elif urlsplit(self.path).path == "/api/health":
                self._send_json(200, {"status": "ok", "mode": "container-service" if service_mode else "local-development"})
            else:
                self._send_json(404, _review_error_payload("NOT_FOUND", "Route not found."))

        def _handle_product_review(self, body: dict[str, Any]) -> None:
            """Handle main's 0.3 product contract without normalizing its result."""
            request_started = time.perf_counter()
            request_id = _safe_error_request_id(body.get("request_id"))
            def send(status, response):
                self._send_json(status, response)
            try:
                request_id, product = context_from_review_request(body)
            except RequestValidationError as exc:
                send(exc.http_status, _review_error_payload(exc.code, str(exc), request_id))
                return
            try:
                # Read .env for each request so a locally restarted or long-lived
                # development server always observes the persisted research gates.
                _load_project_r2_env()
                research_bundle = HmCombinedResearchSource().analyze(product)
                fabric_prices = research_bundle["fabric_prices"]
                fabric_price_error = research_bundle["fabric_price_error"]
                customer_profile = research_bundle["customer_age_profile"]
                customer_error = research_bundle["customer_age_error"]
                target_audience_proposal = None
                target_audience_error = None
                historical_price = research_bundle["historical_price"]
                historical_price_error = research_bundle["historical_price_error"]
                if hm_research_noncommercial_enabled():
                    if historical_price is not None:
                        fx_started = time.perf_counter()
                        try:
                            fx = fetch_usd_krw_reference_rate()
                            scaled_mean = historical_price.get("scaled_price_statistics", {}).get("mean")
                            conversion = convert_usd_to_krw(scaled_mean, fx["rate"])
                            historical_price["usd_krw_conversion"] = {**fx, **conversion}
                        except (CurrencyConversionError, ValueError) as exc:
                            historical_price["usd_krw_conversion"] = {
                                "status": "error", "message": str(exc),
                            }
                        finally:
                            _LOGGER.info("P07 FX reference lookup and conversion completed in %.3fs",
                                         time.perf_counter() - fx_started)
                target_audience_authorized = target_llm_transfer_authorized()
                if target_audience_authorized:
                    llm_started = time.perf_counter()
                    try:
                        target_audience_proposal = propose_target_audience(
                            product_context=product, customer_profile=customer_profile,
                            fabric_listing_reference=list(fabric_prices),
                            client=OpenAIResponsesClient(timeout_seconds=120, max_output_tokens=3200),
                        )
                    except (LlmCallError, ValueError) as exc:
                        target_audience_error = str(exc)
                    finally:
                        _LOGGER.info("P07 optional target-audience LLM completed in %.3fs", time.perf_counter() - llm_started)
                review_started = time.perf_counter()
                review = build_sales_market_review(
                    request_id, producer_id=AGENT_ID, product_context=product,
                    fabric_price_analysis=fabric_prices,
                    fabric_price_error=fabric_price_error,
                    fabric_price_source_status=research_bundle.get("fabric_price_source_status"),
                    historical_price_source_status=research_bundle.get("historical_price_source_status"),
                    historical_price_analysis=historical_price,
                    historical_price_error=historical_price_error,
                    customer_age_profile=customer_profile,
                    customer_age_error=customer_error,
                    customer_age_source_status=research_bundle.get("customer_age_source_status"),
                    target_audience_proposal=target_audience_proposal,
                ).to_dict()
                review["target_audience"]["transfer_authorized"] = target_audience_authorized
                review["target_audience"]["llm_call_status"] = (
                    "completed" if target_audience_proposal is not None else
                    "failed" if target_audience_error else "not_authorized"
                )
                if target_audience_error:
                    review["errors"].append({"code": "TARGET_AUDIENCE_LLM_FAILED", "message": target_audience_error})
                    if review["status"] == "ok":
                        review["status"] = "partial"
                review = _p08_review_projection(review)
                _validate_review_result(review, request_id)
                _LOGGER.info("P07 review assembly and contract validation completed in %.3fs",
                             time.perf_counter() - review_started)
                send(200, {
                    "contract_version": body.get("contract_version", REVIEW_CONTRACT_VERSION),
                    "request_id": request_id,
                    "producer": {"agent_id": AGENT_ID, "schema_version": REVIEW_SCHEMA_VERSION},
                    "status": review["status"],
                    "result": review,
                })
                _LOGGER.info("P07 request completed in %.3fs", time.perf_counter() - request_started)
                return
            except (ConnectionError, TimeoutError, OSError) as exc:
                _LOGGER.error("P07 review unavailable: request_id=%s exception_class=%s",
                              request_id, type(exc).__name__)
                send(503, _review_error_payload("SERVICE_UNAVAILABLE", "Project_07 is temporarily unavailable.", request_id))
            except Exception as exc:
                _LOGGER.error("P07 review failed: request_id=%s exception_class=%s",
                              request_id, type(exc).__name__)
                send(500, _review_error_payload("REVIEW_FAILED", "Project_07 review failed.", request_id))
        def do_POST(self) -> None:
            if unquote(urlsplit(self.path).path) != REVIEW_PATH:
                self._send_json(404, _review_error_payload("NOT_FOUND", "Route not found."))
                return
            request_id = None
            if not self._authorized():
                self._send_json(401, _review_error_payload("UNAUTHORIZED", "Bearer authentication required."))
                return
            if self.headers.get_content_type() != "application/json":
                self._send_json(415, _review_error_payload("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json."))
                return
            if self.headers.get("Transfer-Encoding"):
                self._send_json(400, _review_error_payload("UNSUPPORTED_TRANSFER_ENCODING", "Transfer-Encoding is unsupported."))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise RequestValidationError("EMPTY_BODY", "A JSON body is required.", 400)
                if length > MAX_BODY_BYTES:
                    raise RequestValidationError("REQUEST_TOO_LARGE", "Review body exceeds the limit.", 413)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise RequestValidationError("INCOMPLETE_BODY", "Incomplete body.", 400)
                body = json.loads(raw, parse_constant=_reject_json_constant)
            except RequestValidationError as exc:
                self._send_json(exc.http_status, _review_error_payload(exc.code, str(exc), request_id))
                return
            except (ValueError, json.JSONDecodeError, RecursionError):
                self._send_json(400, _review_error_payload("INVALID_JSON", "Request body must be valid JSON."))
                return
            if isinstance(body, dict):
                request_id = _safe_error_request_id(body.get("request_id"))
            if not isinstance(body, dict) or body.get("contract_version") != REVIEW_CONTRACT_VERSION:
                self._send_json(422, _review_error_payload("UNSUPPORTED_CONTRACT_VERSION", "This route accepts vogue-sales-market-review/0.3-proposal.", request_id))
                return
            self._handle_product_review(body)

    return Project07Handler


def serve(host: str = "127.0.0.1", port: int = 8766, *, service_mode: bool = False) -> None:
    _load_project_r2_env()
    if service_mode:
        if host != "0.0.0.0":
            raise ValueError("Container service mode must bind to 0.0.0.0.")
        if not os.environ.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "").strip():
            raise ValueError("Bearer authentication is required in container service mode.")
    elif host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Non-loopback bind requires container service mode.")
    server = ThreadingHTTPServer((host, port), make_handler(service_mode=service_mode))
    print(f"Project_07 sales-market review service: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-mode", action="store_true", help="Allow container bind; requires bearer auth.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    serve(args.host or ("0.0.0.0" if args.service_mode else "127.0.0.1"), args.port,
          service_mode=args.service_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
