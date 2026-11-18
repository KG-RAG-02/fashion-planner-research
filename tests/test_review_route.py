"""An orchestrator request reaches the review handler."""
import email.message
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from fashion_planner_research.http_server import make_handler, REVIEW_PATH

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-only-token"


class CurrentReviewRouteTests(unittest.TestCase):
    def test_orchestrator_product_request_uses_review_api_without_artifact_store(self):
        request = json.loads((ROOT / "contracts/examples/research-review.request.json").read_text())
        request_schema = json.loads((ROOT / "contracts/schemas/research-review-request.schema.json").read_text())
        response_schema = json.loads((ROOT / "contracts/schemas/research-review-response.schema.json").read_text())
        Draft202012Validator(request_schema).validate(request)
        research = {
            "fabric_prices": (), "fabric_price_error": None,
            "fabric_price_source_status": "not_configured",
            "customer_age_profile": None, "customer_age_error": None,
            "historical_price": None, "historical_price_error": None,
        }
        handler_type = make_handler()
        handler = object.__new__(handler_type)
        handler.path = REVIEW_PATH
        handler.command = "POST"
        handler.headers = email.message.Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = str(len(json.dumps(request).encode()))
        handler.headers["Authorization"] = f"Bearer {TOKEN}"
        handler.rfile = io.BytesIO(json.dumps(request).encode())
        handler.wfile = io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = lambda status: setattr(handler, "status", status), lambda *_: None, lambda: None
        env = {"FASHION_PLANNER_SERVICE_TOKEN": TOKEN, "RESEARCH_DISABLE_DOTENV": "1",
               "RESEARCH_HM_NONCOMMERCIAL_RESEARCH": "0", "RESEARCH_TARGET_LLM_TRANSFER_AUTHORIZED": "0"}
        with patch.dict(os.environ, env, clear=True), patch(
            "fashion_planner_research.http_server.HmCombinedResearchSource.analyze", return_value=research,
        ) as source:
            handler_type.do_POST(handler)
        response = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.status, 200)
        self.assertEqual(response["contract_version"], "research-handoff")
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["result"]["target_audience"]["llm_call_status"], "not_authorized")
        self.assertIsNone(response["result"]["target_audience"]["prompt_sha256"])
        Draft202012Validator(response_schema).validate(response)
        source.assert_called_once_with(request["input"]["product"])

    def test_published_response_and_error_payloads_match_their_schemas(self):
        response_example = json.loads((ROOT / "contracts/examples/research-review.response.json").read_text())
        response_schema = json.loads((ROOT / "contracts/schemas/research-review-response.schema.json").read_text())
        error_schema = json.loads((ROOT / "contracts/schemas/research-review-error.schema.json").read_text())
        Draft202012Validator(response_schema).validate(response_example)
        Draft202012Validator(error_schema).validate({
            "contract_version": "research-handoff",
            "request_id": "review-contract-check",
            "error": {"code": "UPSTREAM_UNAVAILABLE", "message": "Source unavailable."},
        })
