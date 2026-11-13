"""Loopback development HTTP adapter for PlanningDraft sales-market reviews.

It supports explicit local source configuration from process environment. It
remains a development adapter, not a production deployment or retry policy.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from .domain.models import MarketResearchContext, MarketResearchResult, ResultStatus, normalize_no_results_payload
from .domain.currency_conversion import CurrencyConversionError, convert_usd_to_krw, fetch_usd_krw_reference_rate
from .domain.sales_market_review import REVIEW_SCHEMA_VERSION, build_sales_market_review
from .llm.target_audience import propose_target_audience
from .llm.responses_api import LlmCallError, OpenAIResponsesClient
from .sources.sales.hm_kaggle.hm_combined_research_source import HmCombinedResearchSource


REVIEW_CONTRACT_VERSION = "vogue-sales-market-review/0.2-proposal"
AGENT_ID = "sales-market-agent"
CANONICAL_ROUTE_ID = "sales-market-agent"
MAX_BODY_BYTES = 1_048_576
REVIEW_PATH = f"/v1/agents/{CANONICAL_ROUTE_ID}/reviews"



def _safe_error_request_id(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(char.isalnum() or char in "-_.:" for char in value)
    ):
        return value
    return None


def _review_error_payload(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    return {
        "contract_version": REVIEW_CONTRACT_VERSION,
        "request_id": request_id,
        "error": {"code": code, "message": message},
    }


class RequestValidationError(ValueError):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def context_from_review_request(value: Any) -> tuple[str, dict[str, Any]]:
    """Validate and project the minimal product payload accepted from P06."""
    if not isinstance(value, dict) or set(value) != {"contract_version", "request_id", "input"}:
        raise RequestValidationError("INVALID_ENVELOPE", "Review request envelope is invalid.")
    if value.get("contract_version") != REVIEW_CONTRACT_VERSION:
        raise RequestValidationError("UNSUPPORTED_CONTRACT_VERSION", "Unsupported review contract_version.")
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip() or _safe_error_request_id(request_id) != request_id:
        raise RequestValidationError("INVALID_REQUEST_ID", "request_id is invalid.")
    payload = value.get("input")
    if (not isinstance(payload, dict)
            or set(payload) - {"request_id", "product"}
            or not {"request_id", "product"}.issubset(payload)):
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
    _validate_json_tree(product, "input.product")
    # The domain adapter still uses a product wrapper internally. Never pass
    # through the producer's PlanningDraft or metadata beyond these fields.
    return request_id, {"product": product}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def _validate_json_tree(value: Any, field: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise RequestValidationError("INVALID_INPUT", f"{field} must contain valid JSON values.") from None


def make_handler(
) -> type[BaseHTTPRequestHandler]:
    """Create a handler dedicated to PlanningDraft post-review."""

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
            normalize_no_results: bool = True,
        ) -> None:
            payload = normalize_no_results_payload(body) if normalize_no_results else body
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/health":
                health = {"status": "ok", "mode": "local-development"}
                self._send_json(200, health)
            else:
                self._send_json(404, _review_error_payload("NOT_FOUND", "Route not found."))

        def _authorized(self) -> bool:
            expected = os.environ.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "")
            authorization = self.headers.get("Authorization", "")
            supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
            return bool(expected and supplied) and hmac.compare_digest(supplied, expected)

        def do_POST(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if path != REVIEW_PATH:
                self._send_json(404, _review_error_payload("NOT_FOUND", "Route not found."))
                return
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
                request_id, planning_draft = context_from_review_request(body)
                context = MarketResearchContext(request_id=request_id, planning_request={})
            except RequestValidationError as exc:
                request_id = _safe_error_request_id(body.get("request_id")) if "body" in locals() and isinstance(body, dict) else None
                self._send_json(exc.http_status, _review_error_payload(exc.code, str(exc), request_id))
                return
            except (ValueError, json.JSONDecodeError, RecursionError):
                self._send_json(400, _review_error_payload("INVALID_JSON", "Request body must be valid JSON."))
                return
            try:
                # Read .env for each request so a locally restarted or long-lived
                # development server always observes the persisted research gates.
                _load_project_r2_env()
                result = MarketResearchResult(
                    schema_version="1.0",
                    request_id=context.request_id,
                    agent_id=AGENT_ID,
                    status=ResultStatus.NO_RESULTS,
                    summary="Post-planning review is based only on connected, explicitly configured sources.",
                    limitations=(),
                )
                product = planning_draft.get("product", {})
                research_bundle = HmCombinedResearchSource().analyze(product)
                fabric_prices = research_bundle["fabric_prices"]
                fabric_price_error = research_bundle["fabric_price_error"]
                customer_profile = research_bundle["customer_age_profile"]
                customer_error = research_bundle["customer_age_error"]
                target_audience_proposal = None
                target_audience_error = None
                historical_price = research_bundle["historical_price"]
                historical_price_error = research_bundle["historical_price_error"]
                if os.getenv("VOGUE_HM_RESEARCH_NONCOMMERCIAL", "").strip().casefold() in {"1", "true", "yes"}:
                    if historical_price is not None:
                        try:
                            fx = fetch_usd_krw_reference_rate()
                            scaled_mean = historical_price.get("scaled_price_statistics", {}).get("mean")
                            conversion = convert_usd_to_krw(scaled_mean, fx["rate"])
                            historical_price["usd_krw_conversion"] = {**fx, **conversion}
                        except (CurrencyConversionError, ValueError) as exc:
                            historical_price["usd_krw_conversion"] = {
                                "status": "error", "message": str(exc),
                            }
                target_audience_authorized = os.getenv("VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED", "").strip().casefold() in {"1", "true", "yes"}
                if target_audience_authorized:
                    try:
                        target_audience_proposal = propose_target_audience(
                            product_context=product, customer_profile=customer_profile,
                            market_price_reference=historical_price,
                            fabric_listing_reference=list(fabric_prices),
                            client=OpenAIResponsesClient(timeout_seconds=120, max_output_tokens=3200),
                        )
                    except (LlmCallError, ValueError) as exc:
                        target_audience_error = str(exc)
                review = build_sales_market_review(
                    result, planning_draft=planning_draft,
                    fabric_price_analysis=fabric_prices,
                    fabric_price_error=fabric_price_error,
                    historical_price_analysis=historical_price,
                    historical_price_error=historical_price_error,
                    customer_age_profile=customer_profile,
                    customer_age_error=customer_error,
                    target_audience_proposal=target_audience_proposal,
                ).to_dict()
                review["target_audience"]["transfer_authorized"] = target_audience_authorized
                review["target_audience"]["llm_call_status"] = (
                    "completed" if target_audience_proposal is not None else
                    "failed_or_not_configured" if target_audience_authorized else "not_authorized"
                )
                if target_audience_error:
                    review["errors"].append({"code": "TARGET_AUDIENCE_LLM_FAILED", "message": target_audience_error})
                if (
                    review.get("schema_version") != REVIEW_SCHEMA_VERSION
                    or review.get("request_id") != context.request_id
                    or review.get("producer_id") != AGENT_ID
                    or review.get("status") not in {"ok", "partial", "no_results", "error"}
                ):
                    raise RuntimeError("Review result violates its local proposal contract.")
                self._send_json(200, {
                    "contract_version": REVIEW_CONTRACT_VERSION,
                    "request_id": context.request_id,
                    "producer": {"agent_id": AGENT_ID, "schema_version": REVIEW_SCHEMA_VERSION},
                    "status": review["status"],
                    "result": review,
                }, normalize_no_results=False)
                return
            except (ConnectionError, TimeoutError, OSError):
                self._send_json(503, _review_error_payload("SERVICE_UNAVAILABLE", "Project_07 is temporarily unavailable.", context.request_id))
            except Exception:
                self._send_json(500, _review_error_payload("REVIEW_FAILED", "Project_07 review failed.", context.request_id))

    return Project07Handler


def _load_project_r2_env() -> None:
    """Load R2, H&M research, and image-egress feature settings from local .env."""
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
        if not separator or not (key.startswith("R2_") or key.startswith("VOGUE_HM_")
                                 or key.startswith("PROJECT07_HM_")
                                 or key == "PROJECT07_R2_SOURCE_MANIFEST"
                                 or key == "VOGUE_IMAGE_LLM_TRANSFER_AUTHORIZED"
                                 or key == "VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED"
                                 or key == "VOGUE_USD_KRW_FALLBACK_RATE"
                                 or key == "VOGUE_USD_KRW_FALLBACK_DATE"
                                 or key == "OPENAI_API_KEY"
                                 or key == "LLM_API_KEY"
                                 or key == "OPENAI_MODEL"
                                 or key == "VOGUE_DUCKDB_EXTENSION_DIRECTORY"
                                 or key == "PROJECT07_FABRIC_PRICE_R2_KEY"):
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
) -> None:
    """Start the loopback PlanningDraft review server."""
    _load_project_r2_env()
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The local proposal server may only bind to loopback.")
    server = ThreadingHTTPServer((host, port), make_handler())
    print(f"Project_07 sales-market review service: http://{host}:{port}")
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
    args = parser.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
