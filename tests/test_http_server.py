"""HTTP tests for the PlanningDraft post-review API."""

from __future__ import annotations

import io
import json
import os
import unittest
from email.message import Message
from unittest.mock import patch

from market_research_agent.http_server import make_handler


class Project07ReviewHttpTests(unittest.TestCase):
    def request(self, method: str, path: str, payload=None, *, token="test-token"):
        handler_type = make_handler()
        handler = object.__new__(handler_type)
        handler.path = path
        handler.command = method
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        if token:
            handler.headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(payload).encode() if payload is not None else b""
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.response_status = None
        handler.response_headers = {}
        handler.send_response = lambda status, *args: setattr(handler, "response_status", status)
        handler.send_header = lambda name, value: handler.response_headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        with patch.dict(os.environ, {"VOGUE_AGENT_SERVICE_BEARER_TOKEN": "test-token"}):
            (handler_type.do_POST if method == "POST" else handler_type.do_GET)(handler)
        output = handler.wfile.getvalue()
        return handler.response_status, json.loads(output) if output else None

    def valid_review(self):
        return {
            "contract_version": "vogue-sales-market-review/0.1-proposal",
            "request_id": "review-http-001",
            "input": {
                "request_id": "review-http-001",
                "planning_draft": {
                    "schema_version": "1.0",
                    "draft_id": "draft-http-001",
                    "product": {
                        "category": "Outerwear", "product_type": "Jacket",
                        "gender": "women", "proposed_price": 129000, "currency": "KRW",
                    },
                },
            },
        }

    def test_review_route_returns_review_and_human_decision_support(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["producer"]["agent_id"], "sales-market-agent")
        self.assertEqual(response["request_id"], "review-http-001")
        self.assertTrue(response["result"]["decision_support"]["human_decision_required"])

    def test_review_requires_bearer_authentication(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(), token=None,
        )
        self.assertEqual(status, 401)
        self.assertEqual(response["error"]["code"], "UNAUTHORIZED")

    def test_legacy_research_endpoints_are_removed(self):
        for route in (
            "/v1/agents/sales-market-agent/runs",
            "/v1/agents/sales-market-agent/structured-runs",
            "/v1/agents/sales-market-agent/simulation-runs",
        ):
            with self.subTest(route=route):
                status, response = self.request("POST", route, self.valid_review())
                self.assertEqual(status, 404)
                self.assertEqual(response["error"]["code"], "NOT_FOUND")

    def test_review_rejects_mismatched_request_ids(self):
        payload = self.valid_review()
        payload["input"]["request_id"] = "different-id"
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", payload,
        )
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "REQUEST_ID_MISMATCH")


if __name__ == "__main__":
    unittest.main()
