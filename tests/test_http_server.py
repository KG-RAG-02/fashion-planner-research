"""HTTP tests for the minimal product review API."""

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
        research_bundle = {
            "fabric_prices": (), "fabric_price_error": None,
            "customer_age_profile": None, "customer_age_error": None,
            "historical_price": None, "historical_price_error": None,
        }
        with (
            patch.dict(os.environ, {
                "VOGUE_AGENT_SERVICE_BEARER_TOKEN": "test-token",
                "VOGUE_PROJECT07_DISABLE_DOTENV": "true",
                "VOGUE_HM_RESEARCH_NONCOMMERCIAL": "false",
                "VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED": "false",
            }),
            patch("market_research_agent.http_server.HmCombinedResearchSource.analyze", return_value=research_bundle),
        ):
            (handler_type.do_POST if method == "POST" else handler_type.do_GET)(handler)
        output = handler.wfile.getvalue()
        return handler.response_status, json.loads(output) if output else None

    def valid_review(self):
        return {
            "contract_version": "vogue-sales-market-review/0.2-proposal",
            "request_id": "review-http-001",
            "input": {
                "request_id": "review-http-001",
                "product": {
                    "category": "Outerwear", "product_type": "Jacket",
                    "gender": "women", "season": "winter", "material": "wool blend",
                },
            },
        }

    def test_review_route_returns_review_and_human_decision_support(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["contract_version"], "vogue-sales-market-review/0.2-proposal")
        self.assertEqual(response["producer"]["agent_id"], "sales-market-agent")
        self.assertEqual(response["producer"]["schema_version"], "sales-market-review/0.1-internal")
        self.assertEqual(response["request_id"], "review-http-001")
        self.assertEqual(response["status"], response["result"]["status"])
        self.assertEqual(response["result"]["request_id"], response["request_id"])
        self.assertEqual(response["result"]["producer_id"], response["producer"]["agent_id"])
        self.assertEqual(response["result"]["schema_version"], response["producer"]["schema_version"])
        self.assertEqual(response["result"]["draft_id"], None)
        self.assertEqual(response["result"]["draft_context"], {
            "category": "Outerwear", "product_type": "Jacket", "gender": "women",
            "season": "winter", "material": "wool blend",
        })
        self.assertTrue(response["result"]["decision_support"]["human_decision_required"])

    def test_category_only_minimum_product_request_is_accepted(self):
        payload = self.valid_review()
        payload["input"]["product"] = {"category": "Outerwear"}
        status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["draft_context"], {"category": "Outerwear"})

    def test_optional_product_fields_may_be_missing_or_null(self):
        payload = self.valid_review()
        payload["input"]["product"] = {
            "category": "Outerwear", "product_type": None, "gender": None,
            "season": None, "material": None,
        }
        status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["draft_context"], {"category": "Outerwear"})

    def test_category_is_required(self):
        payload = self.valid_review()
        payload["input"]["product"] = {"product_type": "Jacket"}
        status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "INVALID_PRODUCT")
        self.assertEqual(response["contract_version"], "vogue-sales-market-review/0.2-proposal")

    def test_gender_must_use_supported_enum(self):
        payload = self.valid_review()
        payload["input"]["product"]["gender"] = "WOMEN"
        status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "INVALID_GENDER")

    def test_price_costing_image_and_evidence_fields_are_rejected(self):
        for field, value in (
            ("proposed_price", 129000), ("currency", "KRW"), ("costing", {}),
            ("image_ref", {}), ("evidence", {"private": "metadata"}),
        ):
            with self.subTest(field=field):
                payload = self.valid_review()
                payload["input"]["product"][field] = value
                status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
                self.assertEqual(status, 422)
                self.assertEqual(response["error"]["code"], "INVALID_PRODUCT")

    def test_planning_draft_wrapper_and_image_flag_are_rejected(self):
        for field, value in (
            ("planning_draft", {}), ("include_image_style_analysis", True),
            ("evidence", {"producer_metadata": "do not forward"}),
        ):
            with self.subTest(field=field):
                payload = self.valid_review()
                payload["input"][field] = value
                status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
                self.assertEqual(status, 422)
                self.assertEqual(response["error"]["code"], "INVALID_INPUT")

    def test_review_requires_bearer_authentication(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(), token=None,
        )
        self.assertEqual(status, 401)
        self.assertEqual(response["contract_version"], "vogue-sales-market-review/0.2-proposal")
        self.assertIsNone(response["request_id"])
        self.assertEqual(response["error"]["code"], "UNAUTHORIZED")
        self.assertTrue(response["error"]["message"])

    def test_previous_contract_version_is_rejected_explicitly(self):
        payload = self.valid_review()
        payload["contract_version"] = "vogue-sales-market-review/0.1-proposal"
        status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
        self.assertEqual(status, 422)
        self.assertEqual(response["contract_version"], "vogue-sales-market-review/0.2-proposal")
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_CONTRACT_VERSION")

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
