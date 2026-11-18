"""RESEARCH_REVIEW_SOURCE=synthetic: 네트워크 없이 결정적인 합성 리뷰를 낸다."""
from __future__ import annotations

import copy
import io
import json
import os
import socket
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from fashion_planner_research.http_server import REVIEW_PATH, make_handler, serve
from fashion_planner_research.sources import synthetic_review_source as synthetic
from fashion_planner_research.sources.market.fabric_price_r2_source import _REQUIRED_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
REQUEST = json.loads((ROOT / "contracts/examples/research-review.request.json").read_text())
RESPONSE_SCHEMA = json.loads(
    (ROOT / "contracts/schemas/research-review-response.schema.json").read_text()
)
ENV = {
    "FASHION_PLANNER_SERVICE_TOKEN": "test-token",
    "RESEARCH_DISABLE_DOTENV": "1",
    synthetic.REVIEW_SOURCE_ENV: synthetic.SYNTHETIC,
}


def _no_network(*_args, **_kwargs):
    raise AssertionError("synthetic mode must not open a network connection")


def _post(payload: dict, env: dict) -> tuple[int, dict]:
    handler_type = make_handler()
    handler = object.__new__(handler_type)
    body = json.dumps(payload).encode()
    handler.path = REVIEW_PATH
    handler.command = "POST"
    handler.headers = Message()
    handler.headers["Content-Type"] = "application/json"
    handler.headers["Authorization"] = "Bearer test-token"
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    status: dict[str, int] = {}
    handler.send_response = lambda code, *args: status.__setitem__("code", code)
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    with patch.dict(os.environ, env, clear=True), \
            patch.object(socket.socket, "connect", _no_network), \
            patch("fashion_planner_research.sources.shared.r2_object_store.R2ObjectStore.from_env",
                  side_effect=AssertionError("synthetic mode must not create a storage client")):
        handler_type.do_POST(handler)
    return status["code"], json.loads(handler.wfile.getvalue())


def _request(request_id: str, **product) -> dict:
    payload = copy.deepcopy(REQUEST)
    payload["request_id"] = payload["input"]["request_id"] = request_id
    payload["input"]["product"].update(product)
    return payload


def _without_request_id(response: dict) -> dict:
    response = copy.deepcopy(response)
    response.pop("request_id")
    response["result"].pop("request_id")
    return response


class SyntheticReviewSourceTests(unittest.TestCase):
    def test_review_is_ok_offline_and_labelled_synthetic(self):
        status, response = _post(_request("synthetic-1"), ENV)
        self.assertEqual(status, 200)
        Draft202012Validator(RESPONSE_SCHEMA).validate(response)
        result = response["result"]
        self.assertEqual(response["status"], "ok")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["missing_data"], [])
        self.assertIn("합성(synthetic)", result["limitations"][0])
        self.assertTrue(all("H&M" not in item for item in result["limitations"]))
        self.assertIsInstance(result["price_analysis"]["proposed_price"], int)
        self.assertEqual(result["target_audience"]["llm_call_status"], synthetic.LLM_CALL_STATUS)
        self.assertFalse(result["target_audience"]["transfer_authorized"])
        self.assertTrue(result["target_audience"]["proposal"]["proposal"].startswith("[합성]"))
        data_evidence = [item for item in result["evidence"]
                         if item["source_type"] not in {"usd_krw_reference_rate", "industry_gross_margin_benchmark"}]
        self.assertEqual(len(data_evidence), 3)
        for item in data_evidence:
            self.assertEqual(item["source_type"], synthetic.SOURCE_TYPE)
            self.assertEqual(item["permission_status"], synthetic.PERMISSION_STATUS)
            self.assertTrue(item["locator"].startswith("synthetic://"))
        for row in (*result["category_price_comparison"], *result["customer_segment_analysis"],
                    *result["material_cost_analysis"]):
            self.assertEqual(row["source_type"], synthetic.SOURCE_TYPE)
            self.assertFalse(row["operational_eligible"])

    def test_same_product_gives_the_same_bytes_apart_from_request_id(self):
        _, first = _post(_request("synthetic-a"), ENV)
        _, second = _post(_request("synthetic-b"), ENV)
        self.assertEqual(
            json.dumps(_without_request_id(first), ensure_ascii=False, sort_keys=True),
            json.dumps(_without_request_id(second), ensure_ascii=False, sort_keys=True),
        )

    def test_category_and_korean_alias_select_different_rows(self):
        _, outerwear = _post(_request("synthetic-o", category="아우터", product_type="재킷"), ENV)
        _, tops = _post(_request("synthetic-t", category="상의", product_type="블라우스"), ENV)
        self.assertEqual(outerwear["status"], "ok")
        self.assertEqual(tops["status"], "ok")
        self.assertNotEqual(outerwear["result"]["price_analysis"]["proposed_price"],
                            tops["result"]["price_analysis"]["proposed_price"])

    def test_material_is_optional(self):
        payload = _request("synthetic-m")
        del payload["input"]["product"]["material"]
        del payload["input"]["product_evidence"]["material"]
        status, response = _post(payload, ENV)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["result"]["material_cost_analysis"], [])

    def test_unsupported_value_is_refused(self):
        env = {**ENV, synthetic.REVIEW_SOURCE_ENV: "fixtures"}
        status, response = _post(_request("synthetic-x"), env)
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "REVIEW_SOURCE_UNSUPPORTED")
        with patch.dict(os.environ, env, clear=True), \
                patch("fashion_planner_research.http_server.ThreadingHTTPServer") as server_type:
            with self.assertRaises(synthetic.UnsupportedReviewSource):
                serve()
        server_type.assert_not_called()

    def test_unset_value_keeps_the_configured_sources(self):
        env = {key: value for key, value in ENV.items() if key != synthetic.REVIEW_SOURCE_ENV}
        bundle = {
            "fabric_prices": (), "fabric_price_error": None, "fabric_price_source_status": "not_configured",
            "customer_age_profile": None, "customer_age_error": None,
            "historical_price": None, "historical_price_error": None,
        }
        with patch("fashion_planner_research.http_server.HmCombinedResearchSource.analyze",
                   return_value=bundle) as analyze:
            status, response = _post(_request("synthetic-off"), env)
        analyze.assert_called_once()
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "no_results")

    def test_fabric_rows_use_the_documented_columns(self):
        self.assertEqual(set(synthetic._FABRIC_COLUMNS), _REQUIRED_COLUMNS)
        self.assertEqual(len(synthetic._FABRIC_COLUMNS), 24)


if __name__ == "__main__":
    unittest.main()
