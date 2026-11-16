"""Loopback development HTTP adapter for VOGUE handoff v0.1.

It supports explicit local source configuration from process environment. It
remains a development adapter, not a production deployment or retry policy.
"""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import logging
import time
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .resource_paths import resource_root
from .domain.currency_conversion import CurrencyConversionError, convert_usd_to_krw, fetch_usd_krw_reference_rate
from .domain.sales_market_review import REVIEW_SCHEMA_VERSION, build_sales_market_review
from .llm.target_audience import propose_target_audience
from .llm.responses_api import LlmCallError, OpenAIResponsesClient
from .runtime_settings import hm_research_noncommercial_enabled, target_llm_transfer_authorized
from .sources.sales.hm_kaggle.hm_combined_research_source import HmCombinedResearchSource
from .artifact_reader import artifact_reader_from_env
from .reviews import (REVIEW_PATH, MAX_BODY_BYTES as REVIEW_MAX_BODY_BYTES, SalesMarketReviewService,
                      ReviewArtifactStore, ReviewError, parse_json, parser_for, problem, review_artifact_dir)

from .domain.models import MarketResearchContext, normalize_no_results_payload
from .sources.market.synthetic_query_coverage import SyntheticQueryCoverageSource
from .sources.market.synthetic_r2 import SyntheticR2FixtureBundleStore
from .sources.market.synthetic_review_fixture import SOURCE_MODE as SYNTHETIC_REVIEW_SOURCE, SyntheticReviewFixtureSource
from .sources.sales.hm_kaggle.hm_sales_adapter import HmHistoricalSalesSource
from .sources.sales.hm_kaggle.hm_r2_sales_source import R2HmHistoricalSalesSource
from .sources.sales.hm_kaggle.hm_r2_duckdb_source import R2DuckDBHmHistoricalSalesSource
from .sources.shared.composite import CompositeMarketResearchSource
from .sources.shared.noop import NoConfiguredSources
from .workflows.market_research import MarketResearchAgent
from .query_interpretation import interpret_query, scope_conflicts, unresolved_ambiguities
from .orchestration_request import CanonicalRequestError, context_from_canonical_request
from .synthetic_rag import (
    LIMITATION as SYNTHETIC_LIMITATION,
    SCHEMA_VERSION as SYNTHETIC_SCHEMA_VERSION,
    SYNTHETIC_MARKERS,
    SyntheticFixtureError,
    analyze_synthetic_request,
)


CONTRACT_VERSION = "vogue-handoff/0.1"
AGENT_ID = MarketResearchAgent.agent_id
CANONICAL_ROUTE_ID = "sales-market-agent"
DEPRECATED_ROUTE_IDS = {"market-research-agent"}
SUPPORTED_UPSTREAM_SCHEMA_VERSIONS = {
    "trend_result": {"1.0", "1.1"},
    "inventory_product_result": {"1.0"},
}
MAX_BODY_BYTES = 1_048_576
# POST-only v1 agent routes: any other method gets 405 with "Allow: POST".
LEGACY_POST_ROUTES = frozenset({
    f"/v1/agents/{CANONICAL_ROUTE_ID}/runs",
    f"/v1/agents/{CANONICAL_ROUTE_ID}/structured-runs",
    *(f"/v1/agents/{alias}/runs" for alias in DEPRECATED_ROUTE_IDS),
})
SYNTHETIC_SIMULATION_PATH = "/v1/simulations/synthetic-rag/runs"
AGENT_SIMULATION_PATH = f"/v1/agents/{CANONICAL_ROUTE_ID}/simulation-runs"
SYNTHETIC_SIMULATION_UI_PATH = "/synthetic-simulation"
_WEB_ROOT = resource_root("web")
_SYNTHETIC_UI_FILES = {
    "/synthetic-simulation.js": _WEB_ROOT / "synthetic_simulation.js",
    "/synthetic-simulation.css": _WEB_ROOT / "synthetic_simulation.css",
}

_STRING_FIELDS = (
    "requested_category", "requested_geography", "requested_channel",
    "requested_query", "requested_period", "requested_population",
    "requested_unit", "requested_currency", "requested_measure_type",
    "requested_mapping_id", "requested_mapping_version", "requested_period_grain",
    "requested_document_version",
)
_REQUEST_FIELDS = {"contract_version", "request_id", "input"}
_INPUT_FIELDS = {
    "request_id", "planning_request", "trend_result", "inventory_product_result",
    "requested_metrics", *_STRING_FIELDS,
}


REVIEW_SOURCE_ENV = "VOGUE_P07_REVIEW_SOURCE"


def review_agent_factory(
    default_factory: Callable[[], MarketResearchAgent], environ: Any = None,
) -> Callable[[], MarketResearchAgent]:
    """Select the /reviews source. Unset keeps the configured default sources;
    synthetic_local_fixture uses only the packaged, labelled synthetic bundle."""
    mode = (os.environ if environ is None else environ).get(REVIEW_SOURCE_ENV, "").strip()
    if not mode:
        return default_factory
    if mode == SYNTHETIC_REVIEW_SOURCE:
        return lambda: MarketResearchAgent(SyntheticReviewFixtureSource())
    raise ReviewError("REVIEW_SOURCE_UNSUPPORTED",
                      "VOGUE_P07_REVIEW_SOURCE must be unset or synthetic_local_fixture.", 503)


def _safe_error_request_id(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(char.isalnum() or char in "-_.:" for char in value)
    ):
        return value
    return None


def _error_payload(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "error": {"code": code, "message": message},
    }


class RequestValidationError(ValueError):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def _object_or_none(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RequestValidationError("INVALID_INPUT", f"{field} must be an object or null.")
    return value


def _validate_json_tree(value: Any, field: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise RequestValidationError("INVALID_INPUT", f"{field} must contain valid JSON values.") from None


def context_from_handoff_request(value: Any) -> MarketResearchContext:
    """Validate the v0.1 envelope and map its input to the local context."""
    if not isinstance(value, dict):
        raise RequestValidationError("INVALID_ENVELOPE", "JSON request body must be an object.")
    extra_request_fields = set(value) - _REQUEST_FIELDS
    if extra_request_fields:
        raise RequestValidationError("INVALID_ENVELOPE", "Request contains unsupported top-level fields.")
    if value.get("contract_version") != CONTRACT_VERSION:
        raise RequestValidationError(
            "UNSUPPORTED_CONTRACT_VERSION", "Unsupported contract_version.", 422,
        )
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise RequestValidationError("INVALID_REQUEST_ID", "request_id must be a non-empty string.")
    payload = value.get("input")
    if not isinstance(payload, dict):
        raise RequestValidationError("INVALID_INPUT", "input must be an object.")
    extra_input_fields = set(payload) - _INPUT_FIELDS
    if extra_input_fields:
        raise RequestValidationError("INVALID_INPUT", "input contains unsupported fields.")
    if payload.get("request_id") != request_id:
        raise RequestValidationError("REQUEST_ID_MISMATCH", "Envelope and input request_id must match.", 422)

    planning_request = payload.get("planning_request")
    if not isinstance(planning_request, dict):
        raise RequestValidationError("INVALID_INPUT", "input.planning_request must be an object.")
    if planning_request.get("request_id") != request_id:
        raise RequestValidationError("REQUEST_ID_MISMATCH", "planning_request request_id must match.", 422)
    if planning_request.get("schema_version") != "1.0":
        raise RequestValidationError(
            "UNSUPPORTED_PRODUCER_SCHEMA", "planning_request schema_version must be 1.0.", 422,
        )

    trend_result = _object_or_none(payload.get("trend_result"), "trend_result")
    inventory_result = _object_or_none(payload.get("inventory_product_result"), "inventory_product_result")
    for required in ("trend_result", "inventory_product_result"):
        if required not in payload:
            raise RequestValidationError("INVALID_INPUT", f"input.{required} is required; use null when absent.")
    for field, upstream in (("trend_result", trend_result), ("inventory_product_result", inventory_result)):
        if upstream is not None:
            upstream_id = upstream.get("request_id")
            if upstream_id != request_id:
                raise RequestValidationError(
                    "REQUEST_ID_MISMATCH", f"{field} request_id must match.", 422,
                )
            nested = upstream.get("result")
            if nested is not None and (not isinstance(nested, dict) or nested.get("request_id") != request_id):
                raise RequestValidationError(
                    "REQUEST_ID_MISMATCH", f"{field}.result request_id must match.", 422,
                )
            upstream_body = nested if isinstance(nested, dict) else upstream
            upstream_status = upstream_body.get("status")
            if upstream_status not in {"ok", "partial", "no_results", "error"}:
                raise RequestValidationError(
                    "INVALID_UPSTREAM_STATUS", f"{field} must carry a supported status.", 422,
                )
            for version_owner in (upstream, upstream_body):
                if "schema_version" in version_owner and (
                    not isinstance(version_owner["schema_version"], str)
                    or not version_owner["schema_version"].strip()
                ):
                    raise RequestValidationError(
                        "INVALID_PRODUCER_SCHEMA", f"{field} schema_version must be a non-empty string.", 422,
                    )
                if "schema_version" in version_owner and version_owner["schema_version"] not in SUPPORTED_UPSTREAM_SCHEMA_VERSIONS[field]:
                    raise RequestValidationError(
                        "UNSUPPORTED_PRODUCER_SCHEMA",
                        f"{field} schema_version is not supported by this adapter.",
                        422,
                    )
        _validate_json_tree(upstream, field)
    _validate_json_tree(planning_request, "planning_request")

    metrics = payload.get("requested_metrics", [])
    if not isinstance(metrics, list) or any(not isinstance(item, str) for item in metrics):
        raise RequestValidationError("INVALID_INPUT", "requested_metrics must be an array of strings.")
    scope_values: dict[str, Any] = {}
    for field in _STRING_FIELDS:
        item = payload.get(field)
        if item is not None and not isinstance(item, str):
            raise RequestValidationError("INVALID_INPUT", f"{field} must be a string or null.")
        scope_values[field] = item

    category = scope_values["requested_category"]
    query = scope_values["requested_query"] or planning_request.get("query") or planning_request.get("original_query")
    interpreted = interpret_query(query if isinstance(query, str) else None)
    context_category = None
    if category is None:
        request_context = planning_request.get("context")
        if isinstance(request_context, dict):
            context_category = request_context.get("category")
            if isinstance(context_category, dict):
                context_category = context_category.get("value")
            if isinstance(context_category, str):
                category = context_category
    category = category or interpreted["category"]
    scope_values["requested_geography"] = scope_values["requested_geography"] or interpreted["geography"]
    scope_values["requested_channel"] = scope_values["requested_channel"] or interpreted["channel"]
    scope_values["requested_period"] = scope_values["requested_period"] or interpreted["period"]
    if not metrics:
        metrics = list(interpreted["metrics"])

    explicit_scope = {
        key: value for key, value in {
            "category": scope_values["requested_category"] or (context_category if isinstance(context_category, str) else None),
            "metrics": tuple(payload.get("requested_metrics", ())) or None,
            "geography": payload.get("requested_geography"),
            "channel": payload.get("requested_channel"),
            "period": payload.get("requested_period"),
        }.items() if value not in (None, (), "")
    }
    request_context = planning_request.get("context")
    if isinstance(request_context, dict) and isinstance(request_context.get("gender"), str):
        explicit_scope["gender"] = request_context["gender"]

    return MarketResearchContext(
        request_id=request_id,
        planning_request=planning_request,
        trend_result=trend_result,
        inventory_product_result=inventory_result,
        requested_category=category,
        requested_metrics=tuple(metrics),
        requested_query=query if isinstance(query, str) else None,
        inferred_scope={key: val for key, val in interpreted.items() if val not in (None, ())},
        explicit_scope=explicit_scope,
        query_intent=str(interpreted["intent"]),
        query_ambiguities=unresolved_ambiguities(interpreted, explicit_scope) + scope_conflicts(interpreted, explicit_scope),
        parser_version=str(interpreted["parser_version"]),
        **{key: val for key, val in scope_values.items() if key not in {"requested_category", "requested_query"}},
    )


REVIEW_CONTRACT_VERSION = "vogue-sales-market-review/0.3-proposal"
_LOGGER = logging.getLogger(__name__)

_REVIEW_REQUIRED_FIELDS = frozenset({
    "schema_version", "request_id", "draft_id", "producer_id", "status", "draft_context",
    "category_price_comparison", "material_cost_analysis", "customer_segment_analysis",
    "target_audience_analysis", "margin_estimate", "evidence", "missing_data", "errors",
    "limitations", "decision_support", "price_analysis", "fabric_cost_analysis",
    "margin_analysis", "margin_price_range_analysis", "target_audience",
})


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



def make_handler(
    agent_factory: Callable[[], MarketResearchAgent] | None = None,
    *,
    synthetic_simulation_enabled: bool = False,
    synthetic_bundle_provider: Callable[[], dict[str, Any]] | None = None,
    review_service: SalesMarketReviewService | None = None,
    service_mode: bool = False,
) -> type[BaseHTTPRequestHandler]:
    """Create a handler; simulation routes are isolated behind an explicit flag.

    ``service_mode`` only changes the mode reported by /api/health; binding and
    authentication rules are enforced by serve() and the routes themselves.
    """
    build_agent = agent_factory or MarketResearchAgent

    class Project07Handler(BaseHTTPRequestHandler):
        server_version = "Project07Local/0.1"

        def log_message(self, fmt: str, *args: object) -> None:
            # Do not write request bodies or URL query data to the default log.
            return

        def _send_json(
            self,
            status: int,
            body: dict[str, Any],
            *,
            headers: dict[str, str] | None = None,
            preserve: bool = False,
        ) -> None:
            serialization_started = time.perf_counter()
            encoded = json.dumps(body if preserve else normalize_no_results_payload(body), ensure_ascii=False, allow_nan=False).encode("utf-8")
            serialization_elapsed = time.perf_counter() - serialization_started
            write_started = time.perf_counter()
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
                # A disconnected client cannot receive a second error envelope.
                _LOGGER.info("P07 response delivery interrupted before completion")
                return
            _LOGGER.info(
                "P07 response serialization %.3fs; write %.3fs; bytes=%d",
                serialization_elapsed, time.perf_counter() - write_started, len(encoded),
            )


        def _method_not_allowed(self) -> bool:
            """Answer 405 + Allow for a POST-only v1 agent route; False for any other path."""
            path = unquote(urlsplit(self.path).path)
            message = "Method not allowed; this route accepts POST."
            if path == REVIEW_PATH:
                self._send_json(405, problem("METHOD_NOT_ALLOWED", message), headers={"Allow": "POST"}, preserve=True)
            elif path in LEGACY_POST_ROUTES:
                self._send_json(405, _error_payload("METHOD_NOT_ALLOWED", message), headers={"Allow": "POST"})
            else:
                return False
            return True

        def _other_method(self) -> None:
            # Other paths keep the stdlib answer for methods without a handler.
            if not self._method_not_allowed():
                self.send_error(501, "Unsupported method (%r)" % self.command)

        do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _other_method

        def _send_static(self, path: Path, content_type: str) -> None:
            try:
                content = path.read_bytes()
            except OSError:
                self._send_json(503, _error_payload("UI_UNAVAILABLE", "Simulation UI is unavailable."))
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def _send_simulation_error(self, status: int, reason_code: str, message: str, request_id: str | None = None) -> None:
            self._send_json(status, {
                "schema_version": SYNTHETIC_SCHEMA_VERSION,
                "request_id": request_id,
                "status": "error",
                "reason": reason_code,
                "message": message,
                "execution_mode": "synthetic_test",
                "simulation_mode": "synthetic_simulation",
                **SYNTHETIC_MARKERS,
                "limitations": [SYNTHETIC_LIMITATION],
            })

        def _handle_synthetic_simulation(self) -> None:
            if not synthetic_simulation_enabled:
                self._send_json(404, _error_payload("NOT_FOUND", "Route not found."))
                return
            if not self._authorized():
                self._send_simulation_error(401, "unauthorized", "Bearer authentication required.")
                return
            if self.headers.get_content_type() != "application/json":
                self._send_simulation_error(415, "unsupported_media_type", "Content-Type must be application/json.")
                return
            if self.headers.get("Transfer-Encoding"):
                self._send_simulation_error(400, "unsupported_transfer_encoding", "Transfer-Encoding is unsupported.")
                return
            try:
                raw_length = self.headers.get("Content-Length")
                try:
                    length = int(raw_length) if raw_length is not None else 0
                except ValueError:
                    raise RequestValidationError("INVALID_CONTENT_LENGTH", "Content-Length is invalid.", 400) from None
                if length <= 0:
                    raise RequestValidationError("EMPTY_BODY", "A JSON request body is required.", 400)
                if length > MAX_BODY_BYTES:
                    raise RequestValidationError("REQUEST_TOO_LARGE", "Request body exceeds the local limit.", 413)
                raw_body = self.rfile.read(length)
                if len(raw_body) != length:
                    raise RequestValidationError("INCOMPLETE_BODY", "Request body was incomplete.", 400)
                request = json.loads(raw_body, parse_constant=_reject_json_constant)
                if not isinstance(request, dict):
                    raise RequestValidationError("INVALID_INPUT", "Canonical request must be a JSON object.", 422)
            except RequestValidationError as exc:
                self._send_simulation_error(exc.http_status, exc.code.casefold(), str(exc))
                return
            except (ValueError, json.JSONDecodeError, RecursionError):
                self._send_simulation_error(400, "invalid_json", "Request body must be valid JSON.")
                return

            request_id = _safe_error_request_id(request.get("request_id"))
            try:
                bundle = synthetic_bundle_provider() if synthetic_bundle_provider is not None else None
                result = analyze_synthetic_request(request, mode="synthetic_test", bundle=bundle)
            except CanonicalRequestError:
                self._send_simulation_error(422, "invalid_canonical_request", "Canonical structured request is invalid.", request_id)
                return
            except SyntheticFixtureError:
                self._send_simulation_error(503, "fixture_unavailable_or_invalid", "Synthetic simulation fixture could not be validated.", request_id)
                return
            except Exception:
                self._send_simulation_error(500, "simulation_failed", "Synthetic simulation could not be completed.", request_id)
                return
            result["simulation_mode"] = "synthetic_simulation"
            self._send_json(200, result)

        def do_GET(self) -> None:
            if self._method_not_allowed():
                return
            path = urlsplit(self.path).path
            if path == "/api/health":
                health = {"status": "ok", "mode": "container-service" if service_mode else "local-development"}
                if synthetic_simulation_enabled:
                    health.update({
                        # Service mode is reported independently of the synthetic data markers.
                        "mode": "container-service" if service_mode else "synthetic_simulation",
                        "synthetic": True,
                        "data_nature": "synthetic_test",
                        "permission_status": "authorized_for_synthetic_testing_only",
                        "operational_eligible": False,
                    })
                self._send_json(200, health)
            elif synthetic_simulation_enabled and path in {"/", SYNTHETIC_SIMULATION_UI_PATH}:
                self._send_static(_WEB_ROOT / "synthetic_simulation.html", "text/html; charset=utf-8")
            elif synthetic_simulation_enabled and path in _SYNTHETIC_UI_FILES:
                content_type = mimetypes.guess_type(str(_SYNTHETIC_UI_FILES[path]))[0] or "application/octet-stream"
                self._send_static(_SYNTHETIC_UI_FILES[path], f"{content_type}; charset=utf-8")
            else:
                self._send_json(404, _error_payload("NOT_FOUND", "Route not found."))

        def _authorized(self) -> bool:
            expected = os.environ.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "")
            authorization = self.headers.get("Authorization", "")
            supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
            return bool(expected and supplied) and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))

        def _handle_product_review(self, body: dict[str, Any]) -> None:
            """Handle main's 0.3 product contract without normalizing its result."""
            request_started = time.perf_counter()
            request_id = _safe_error_request_id(body.get("request_id"))
            def send(status, response):
                self._send_json(status, response, preserve=True)
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
                            market_price_reference=historical_price,
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


        def _handle_review(self) -> None:
            request_id = None
            def send(status, body):
                self._send_json(status, body, preserve=True)
            if not self._authorized():
                send(401, problem("UNAUTHORIZED", "Bearer authentication required."))
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ReviewError("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.", 415)
                if self.headers.get("Transfer-Encoding"):
                    raise ReviewError("UNSUPPORTED_TRANSFER_ENCODING", "Transfer-Encoding is unsupported.", 400)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise ReviewError("INVALID_CONTENT_LENGTH", "Invalid Content-Length.", 400) from None
                if length <= 0:
                    raise ReviewError("EMPTY_BODY", "A JSON body is required.", 400)
                if length > REVIEW_MAX_BODY_BYTES:
                    raise ReviewError("REQUEST_TOO_LARGE", "Review body exceeds the limit.", 413)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ReviewError("INCOMPLETE_BODY", "Incomplete body.", 400)
                body = parse_json(raw)
                if isinstance(body, dict):
                    request_id = _safe_error_request_id(body.get("request_id"))
                    if request_id is not None and not request_id.isascii(): request_id = None
                if (isinstance(body, dict)
                        and isinstance(body.get("contract_version"), str)
                        and body["contract_version"].startswith("vogue-sales-market-review/")
                        and body["contract_version"] != REVIEW_CONTRACT_VERSION):
                    self._send_json(422, _review_error_payload("UNSUPPORTED_CONTRACT_VERSION", "Unsupported review contract_version.", request_id), preserve=True)
                    return
                if isinstance(body, dict) and body.get("contract_version") == REVIEW_CONTRACT_VERSION:
                    if len(raw) > MAX_BODY_BYTES:
                        self._send_json(413, _review_error_payload("REQUEST_TOO_LARGE", "Review body exceeds the limit.", request_id), preserve=True)
                        return
                    self._handle_product_review(body)
                    return
                # Version dispatch precedes schema, artifact, store and source work.
                parser_for(body)
                # Retained raw bytes are always verified over HTTP: without
                # VOGUE_UPSTREAM_ARTIFACT_ROOT the reader fails closed (503).
                service = review_service or SalesMarketReviewService(
                    review_agent_factory(build_agent), ReviewArtifactStore(review_artifact_dir()),
                    artifact_reader_from_env())
                code, response = service.review(body, raw)
                send(code, response)
            except ReviewError as exc:
                send(exc.http_status, problem(exc.code, str(exc), request_id, exc.http_status in {503, 504}))
            except TimeoutError:
                send(504, problem("TIMEOUT", "Review source timed out.", request_id, True))
            except (OSError, ConnectionError):
                send(503, problem("SOURCE_UNAVAILABLE", "Review source or artifact store unavailable.", request_id, True))
            except Exception:
                send(500, problem("REVIEW_FAILED", "Review could not be completed.", request_id))

        def do_POST(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if path == REVIEW_PATH:
                self._handle_review()
                return
            if path in {SYNTHETIC_SIMULATION_PATH, AGENT_SIMULATION_PATH}:
                self._handle_synthetic_simulation()
                return
            prefix = "/v1/agents/"
            suffix = "/structured-runs" if path.endswith("/structured-runs") else "/runs"
            if not (path.startswith(prefix) and path.endswith(suffix)):
                self._send_json(404, _error_payload("NOT_FOUND", "Route not found."))
                return
            route_agent = path[len(prefix):-len(suffix)]
            if route_agent not in {CANONICAL_ROUTE_ID, *DEPRECATED_ROUTE_IDS} or "/" in route_agent:
                self._send_json(404, _error_payload("AGENT_NOT_FOUND", "Agent route not found."))
                return
            if suffix == "/structured-runs" and route_agent != CANONICAL_ROUTE_ID:
                self._send_json(404, _error_payload("AGENT_NOT_FOUND", "Structured request route is not available for this agent."))
                return
            if not self._authorized():
                self._send_json(401, _error_payload("UNAUTHORIZED", "Bearer authentication required."))
                return
            if self.headers.get_content_type() != "application/json":
                self._send_json(415, _error_payload("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json."))
                return
            if self.headers.get("Transfer-Encoding"):
                self._send_json(400, _error_payload("UNSUPPORTED_TRANSFER_ENCODING", "Transfer-Encoding is unsupported."))
                return
            try:
                raw_length = self.headers.get("Content-Length")
                try:
                    length = int(raw_length) if raw_length is not None else 0
                except ValueError:
                    raise RequestValidationError("INVALID_CONTENT_LENGTH", "Content-Length is invalid.", 400) from None
                if length <= 0:
                    raise RequestValidationError("EMPTY_BODY", "A JSON request body is required.", 400)
                if length > MAX_BODY_BYTES:
                    raise RequestValidationError("REQUEST_TOO_LARGE", "Request body exceeds the local limit.", 413)
                raw_body = self.rfile.read(length)
                if len(raw_body) != length:
                    raise RequestValidationError("INCOMPLETE_BODY", "Request body was incomplete.", 400)
                body = json.loads(raw_body, parse_constant=_reject_json_constant)
                if suffix == "/structured-runs":
                    try:
                        context = context_from_canonical_request(body)
                    except CanonicalRequestError as exc:
                        raise RequestValidationError("INVALID_CANONICAL_REQUEST", str(exc), 422) from None
                else:
                    context = context_from_handoff_request(body)
            except RequestValidationError as exc:
                request_id = _safe_error_request_id(body.get("request_id")) if "body" in locals() and isinstance(body, dict) else None
                self._send_json(exc.http_status, _error_payload(exc.code, str(exc), request_id))
                return
            except (ValueError, json.JSONDecodeError, RecursionError):
                self._send_json(400, _error_payload("INVALID_JSON", "Request body must be valid JSON."))
                return
            try:
                result = build_agent().run(context).to_dict()
                if (
                    result.get("schema_version") != "1.0"
                    or result.get("request_id") != context.request_id
                    or result.get("agent_id") != AGENT_ID
                    or result.get("status") not in {"ok", "partial", "no_results", "error"}
                ):
                    raise RuntimeError("Producer result violates the v0.1 response contract.")
                response = {
                    "contract_version": CONTRACT_VERSION,
                    "request_id": context.request_id,
                    "producer": {"agent_id": result["agent_id"], "schema_version": result["schema_version"]},
                    "status": result["status"],
                    "result": result,
                }
                mode_headers = ({
                    "X-Simulation-Mode": "synthetic_simulation",
                    "X-Data-Nature": "synthetic_test",
                    "X-Operational-Eligible": "false",
                } if synthetic_simulation_enabled else None)
                if route_agent in DEPRECATED_ROUTE_IDS:
                    self._send_json(200, response, headers={"Deprecation": "true", **(mode_headers or {})})
                else:
                    self._send_json(200, response, headers=mode_headers)
            except (ConnectionError, TimeoutError, OSError):
                self._send_json(503, _error_payload("SERVICE_UNAVAILABLE", "Project_07 is temporarily unavailable.", context.request_id))
            except Exception:
                self._send_json(500, _error_payload("RUN_FAILED", "Project_07 run failed.", context.request_id))

    return Project07Handler


def configured_agent() -> MarketResearchAgent:
    """Build the configured read-only cloud sales and synthetic market sources."""
    _load_project_r2_env()
    sources = []
    hm_mapping_path = resource_root("configs") / "hm-category-map.v1.json"
    mapping_data = json.loads(hm_mapping_path.read_text(encoding="utf-8"))
    hm_map_raw = os.getenv("VOGUE_HM_CATEGORY_MAP", "").strip()
    if hm_map_raw:
        category_map = {}
        for pair in hm_map_raw.split(","):
            source, separator, target = pair.partition("=")
            if not separator or not source.strip() or not target.strip():
                raise ValueError("VOGUE_HM_CATEGORY_MAP must contain comma-separated source=target pairs.")
            category_map[source.strip()] = target.strip()
        mapping_version = os.getenv("VOGUE_HM_MAPPING_VERSION", "").strip() or "project07-hm-category-map/1.0"
    else:
        # Exact product types provide a more precise category than their broad
        # H&M group. The source adapter resolves type mappings before group fallback.
        category_map = {**mapping_data["product_group_name"], **mapping_data["product_type_name"]}
        mapping_version = mapping_data["mapping_version"]
    r2_db_key = (os.getenv("PROJECT07_HM_R2_DB_KEY", "").strip()
                 or os.getenv("R2_DB_KEY", "").strip())
    if r2_db_key:
        sources.append(R2DuckDBHmHistoricalSalesSource.from_env(
            category_map, mapping_version, usage_authorized=True,
        ))
    elif os.getenv("PROJECT07_HM_R2_MANIFEST_KEY", "").strip():
        sources.append(R2HmHistoricalSalesSource.from_env(
            category_map, mapping_version, usage_authorized=True,
        ))
    elif os.getenv("VOGUE_HM_PROCESSED_DIR", "").strip():
        # Explicit local directory is retained for offline/manual development.
        sources.append(HmHistoricalSalesSource(
            Path(os.environ["VOGUE_HM_PROCESSED_DIR"]), category_map, mapping_version,
            usage_authorized=True,
        ))
    r2_configured = all(os.getenv(name, "").strip() for name in (
        "R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
    ))
    if r2_configured:
        bundle_store = SyntheticR2FixtureBundleStore.from_env()
        sources.append(SyntheticQueryCoverageSource(bundle_loader=bundle_store.load_bundle))
    # Missing configuration is no_results; a configured source that fails is an error.
    return MarketResearchAgent(CompositeMarketResearchSource(sources) if sources else NoConfiguredSources())


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
                                 or key.startswith("PROJECT07_SYNTHETIC_R2_")
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


def serve(
    host: str = "127.0.0.1",
    port: int = 8766,
    *,
    synthetic_demo: bool = False,
    enable_synthetic_simulation: bool = False,
    synthetic_source_backend: str = "r2",
    service_mode: bool = False,
) -> None:
    """Start the local proposal server on loopback; run routes require the shared bearer token."""
    _load_project_r2_env()
    if service_mode:
        if host != "0.0.0.0":
            raise ValueError("Container service mode must bind to 0.0.0.0.")
        if not os.environ.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "").strip():
            raise ValueError("Bearer authentication is required in container service mode.")
    elif host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Non-loopback bind requires container service mode.")
    if synthetic_demo and enable_synthetic_simulation:
        raise ValueError("Choose either legacy --synthetic-demo or --enable-synthetic-simulation, not both.")
    if synthetic_source_backend != "r2":
        raise ValueError("Synthetic local fixture mode is unavailable; configure the R2 source.")
    bundle_store = None
    if synthetic_demo or enable_synthetic_simulation:
        bundle_store = SyntheticR2FixtureBundleStore.from_env()
        synthetic_source = SyntheticQueryCoverageSource(bundle_loader=bundle_store.load_bundle)
        agent_factory = lambda: MarketResearchAgent(synthetic_source)
    else:
        agent_factory = configured_agent
    review_agent_factory(agent_factory)  # fail fast on an unsupported VOGUE_P07_REVIEW_SOURCE
    server = ThreadingHTTPServer((host, port), make_handler(
        agent_factory, synthetic_simulation_enabled=enable_synthetic_simulation,
        synthetic_bundle_provider=bundle_store.load_bundle if bundle_store is not None else None,
        service_mode=service_mode,
    ))
    print(f"Project_07 local service (vogue-handoff/0.1): http://{host}:{port}")
    if synthetic_demo:
        print("Synthetic demonstration mode is enabled; every result is labeled synthetic and is not business evidence.")
    if enable_synthetic_simulation:
        print(f"Synthetic mode enabled: standard /runs uses labeled fixtures; detailed API {AGENT_SIMULATION_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-mode", action="store_true", help="Allow container bind; requires bearer auth; deploy inside a private/TLS-terminated network.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--synthetic-demo", action="store_true", help="Use only the R2 test-only synthetic query coverage fixture.")
    parser.add_argument("--enable-synthetic-simulation", action="store_true", help="Serve the main UI and stable /runs from labeled synthetic fixtures; also expose detailed simulation analysis.")
    parser.add_argument("--synthetic-source", choices=("r2",), default="r2", help="Synthetic simulation source; only the connected R2 release is supported.")
    args = parser.parse_args(argv)
    serve(args.host or ("0.0.0.0" if args.service_mode else "127.0.0.1"), args.port, synthetic_demo=args.synthetic_demo,
          enable_synthetic_simulation=args.enable_synthetic_simulation,
          synthetic_source_backend=args.synthetic_source, service_mode=args.service_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
