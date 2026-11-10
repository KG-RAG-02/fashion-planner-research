"""Synthetic loopback tests for the Project_07 HTTP proposal adapter."""

from __future__ import annotations

import json
import io
from email.message import Message
from pathlib import Path
import unittest

from market_research_agent.http_server import (
    CONTRACT_VERSION,
    MAX_BODY_BYTES,
    make_handler,
)


REQUEST_ID = "synthetic-p07-http-001"
ROOT = Path(__file__).resolve().parents[1]


def valid_request() -> dict:
    path = ROOT / "examples/fixtures/http_json_handoff/project-07-run.request.synthetic.json"
    return json.loads(path.read_text(encoding="utf-8"))


class Project07HttpServerTests(unittest.TestCase):
    def post(self, request_body: dict | bytes, *, path="/v1/agents/market-research-agent/runs", content_type="application/json"):
        handler_type = make_handler()
        handler = object.__new__(handler_type)
        data = request_body if isinstance(request_body, bytes) else json.dumps(request_body).encode()
        handler.path = path
        handler.headers = Message()
        handler.headers["Content-Type"] = content_type
        handler.headers["Content-Length"] = str(len(data))
        handler.rfile = io.BytesIO(data)
        handler.wfile = io.BytesIO()
        handler.response_status = None
        handler.send_response = lambda status, *args: setattr(handler, "response_status", status)
        handler.send_header = lambda *_args: None
        handler.end_headers = lambda: None
        handler_type.do_POST(handler)
        return handler.response_status, json.loads(handler.wfile.getvalue())

    def test_valid_synthetic_run_returns_envelope_and_original_request_context(self):
        status, response = self.post(valid_request())
        self.assertEqual(status, 200)
        self.assertEqual(response["contract_version"], CONTRACT_VERSION)
        self.assertEqual(response["request_id"], REQUEST_ID)
        self.assertEqual(response["producer"]["agent_id"], "market-research-agent")
        self.assertEqual(response["producer"]["schema_version"], "1.3-proposal")
        self.assertEqual(response["status"], "no_results")
        self.assertEqual(response["result"]["status"], "no_results")
        self.assertEqual(response["result"]["request_id"], REQUEST_ID)
        self.assertEqual(response["result"]["upstream_context"]["trend_result"]["schema_version"], "1.1")
        self.assertEqual(response["artifacts"], [])

    def test_project08_route_alias_is_supported_for_local_proposal_client(self):
        status, response = self.post(
            valid_request(), path="/v1/agents/sales-market-agent/runs",
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["producer"]["agent_id"], "market-research-agent")

    def test_request_id_mismatch_is_rejected_before_agent_run(self):
        request = valid_request()
        request["input"]["trend_result"]["request_id"] = "synthetic-different-id"
        status, response = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "REQUEST_ID_MISMATCH")

    def test_unsupported_contract_and_planning_schema_are_rejected(self):
        request = valid_request()
        request["contract_version"] = "unsupported"
        status, response = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_CONTRACT_VERSION")

        request = valid_request()
        request["input"]["planning_request"]["schema_version"] = "99.0"
        status, response = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_PRODUCER_SCHEMA")

    def test_invalid_upstream_status_and_oversized_body_are_rejected(self):
        request = valid_request()
        request["input"]["trend_result"]["status"] = "unknown"
        status, response = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "INVALID_UPSTREAM_STATUS")

        status, response = self.post(b" " * (MAX_BODY_BYTES + 1))
        self.assertEqual(status, 413)
        self.assertEqual(response["error"]["code"], "REQUEST_TOO_LARGE")

    def test_malformed_json_media_type_and_wrong_route_are_rejected(self):
        status, response = self.post(b"{bad", content_type="application/json")
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "INVALID_JSON")

        status, response = self.post(valid_request(), content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")

        status, response = self.post(valid_request(), path="/v1/agents/other-agent/runs")
        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["code"], "AGENT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
