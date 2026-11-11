"""Loopback development HTTP adapter for VOGUE handoff v0.1.

It supports explicit local source configuration from process environment. It
remains a development adapter, not a production deployment or retry policy.
"""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .domain.models import MarketResearchContext
from .sources.market.synthetic_query_coverage import SyntheticQueryCoverageSource
from .sources.sales.hm_kaggle.hm_sales_adapter import HmHistoricalSalesSource
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
SYNTHETIC_SIMULATION_PATH = "/v1/simulations/synthetic-rag/runs"
SYNTHETIC_SIMULATION_UI_PATH = "/synthetic-simulation"
_WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
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


def make_handler(
    agent_factory: Callable[[], MarketResearchAgent] | None = None,
    *,
    synthetic_simulation_enabled: bool = False,
) -> type[BaseHTTPRequestHandler]:
    """Create a handler; simulation routes are isolated behind an explicit flag."""
    build_agent = agent_factory or MarketResearchAgent

    class Project07Handler(BaseHTTPRequestHandler):
        server_version = "Project07Local/0.1"

        def log_message(self, fmt: str, *args: object) -> None:
            # Do not write request bodies or URL query data to the default log.
            return

        def _send_json(self, status: int, body: dict[str, Any], *, headers: dict[str, str] | None = None) -> None:
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

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
                result = analyze_synthetic_request(request, mode="synthetic_test")
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
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._send_json(200, {"status": "ok", "mode": "local-development"})
            elif synthetic_simulation_enabled and path == SYNTHETIC_SIMULATION_UI_PATH:
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
            return bool(expected and supplied) and hmac.compare_digest(supplied, expected)

        def do_POST(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if path == SYNTHETIC_SIMULATION_PATH:
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
                if route_agent in DEPRECATED_ROUTE_IDS:
                    self._send_json(200, response, headers={"Deprecation": "true"})
                else:
                    self._send_json(200, response)
            except (ConnectionError, TimeoutError, OSError):
                self._send_json(503, _error_payload("SERVICE_UNAVAILABLE", "Project_07 is temporarily unavailable.", context.request_id))
            except Exception:
                self._send_json(500, _error_payload("RUN_FAILED", "Project_07 run failed.", context.request_id))

    return Project07Handler


def configured_agent() -> MarketResearchAgent:
    """Build local sources only from explicit process settings and source gates."""
    sources = []
    hm_map_raw = os.getenv("VOGUE_HM_CATEGORY_MAP", "").strip()
    if hm_map_raw:
        category_map = {}
        for pair in hm_map_raw.split(","):
            source, separator, target = pair.partition("=")
            if not separator or not source.strip() or not target.strip():
                raise ValueError("VOGUE_HM_CATEGORY_MAP must contain comma-separated source=target pairs.")
            category_map[source.strip()] = target.strip()
        sources.append(HmHistoricalSalesSource(
            Path(os.getenv("VOGUE_HM_PROCESSED_DIR", "data/processed/hm-personalized-fashion-recommendations")),
            category_map,
            os.getenv("VOGUE_HM_MAPPING_VERSION", ""),
            usage_authorized=os.getenv("VOGUE_HM_USAGE_AUTHORIZED", "").casefold() == "true",
        ))
    return MarketResearchAgent(CompositeMarketResearchSource(sources) if sources else NoConfiguredSources())


def serve(
    host: str = "127.0.0.1",
    port: int = 8766,
    *,
    synthetic_demo: bool = False,
    enable_synthetic_simulation: bool = False,
) -> None:
    """Start the local proposal server on loopback; run routes require the shared bearer token."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The local proposal server may only bind to loopback.")
    agent_factory = (lambda: MarketResearchAgent(SyntheticQueryCoverageSource())) if synthetic_demo else configured_agent
    if synthetic_demo and enable_synthetic_simulation:
        raise ValueError("Choose either legacy --synthetic-demo or --enable-synthetic-simulation, not both.")
    server = ThreadingHTTPServer((host, port), make_handler(
        agent_factory, synthetic_simulation_enabled=enable_synthetic_simulation,
    ))
    print(f"Project_07 local HTTP API (vogue-handoff/0.1): http://{host}:{port}")
    if synthetic_demo:
        print("Synthetic demonstration mode is enabled; every result is labeled synthetic and is not business evidence.")
    if enable_synthetic_simulation:
        print(f"Synthetic simulation API/UI enabled: {SYNTHETIC_SIMULATION_PATH} and {SYNTHETIC_SIMULATION_UI_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--synthetic-demo", action="store_true", help="Use only the local test-only synthetic query coverage fixture.")
    parser.add_argument("--enable-synthetic-simulation", action="store_true", help="Expose an isolated, authenticated simulation API and UI; does not affect normal HTTP routes.")
    args = parser.parse_args(argv)
    serve(args.host, args.port, synthetic_demo=args.synthetic_demo,
          enable_synthetic_simulation=args.enable_synthetic_simulation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
