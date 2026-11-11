"""Synthetic loopback tests for the Project_07 HTTP proposal adapter."""

from __future__ import annotations

import io
import json
import os
from email.message import Message
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

import market_research_agent.http_server as http_server
from market_research_agent.http_server import (
    MAX_BODY_BYTES,
    RequestValidationError,
    SYNTHETIC_SIMULATION_PATH,
    SYNTHETIC_SIMULATION_UI_PATH,
    AGENT_SIMULATION_PATH,
    context_from_handoff_request,
    make_handler,
    configured_agent,
)
from market_research_agent.query_interpretation import interpret_query
from market_research_agent.sources.market.synthetic_query_coverage import SyntheticQueryCoverageSource
from market_research_agent.workflows.market_research import MarketResearchAgent


REQUEST_ID = "synthetic-p07-http-001"
ROOT = Path(__file__).resolve().parents[1]
RESPONSE_FIELDS = {"contract_version", "request_id", "producer", "status", "result"}


def valid_request() -> dict:
    path = ROOT / "examples/fixtures/http_json_handoff/project-07-run.request.synthetic.json"
    return json.loads(path.read_text(encoding="utf-8"))


def valid_demo_request() -> dict:
    path = ROOT / "examples/fixtures/http_json_handoff/project-07-synthetic-demo.request.json"
    return json.loads(path.read_text(encoding="utf-8"))


def valid_structured_request() -> dict:
    path = ROOT / "examples/fixtures/http_json_handoff/project-07-structured-request.synthetic.json"
    return json.loads(path.read_text(encoding="utf-8"))


class Project07HttpServerTests(unittest.TestCase):
    def test_query_interpretation_extracts_only_clear_supported_terms(self):
        parsed = interpret_query("한국 온라인 여성 아우터 시장 규모 2027년")
        self.assertEqual(parsed["category"], "Outerwear")
        self.assertEqual(parsed["metrics"], ("market_size",))
        self.assertEqual(parsed["geography"], "KR")
        self.assertEqual(parsed["channel"], "online")
        self.assertEqual(parsed["period"], "2027")

        ambiguous = interpret_query("아우터와 신발 시장 규모")
        self.assertIsNone(ambiguous["category"])

    def test_http_context_uses_query_hints_without_assuming_default_region_or_channel(self):
        request = valid_request()
        request["input"]["planning_request"]["query"] = "일본 오프라인 니트 판매 지수 2028"
        for key in ("requested_category", "requested_geography", "requested_channel", "requested_metrics"):
            request["input"].pop(key, None)
        request["input"]["planning_request"].get("context", {}).pop("category", None)
        context = context_from_handoff_request(request)
        self.assertEqual(context.requested_category, "Knitwear")
        self.assertEqual(context.requested_geography, "JP")
        self.assertEqual(context.requested_channel, "offline")
        self.assertEqual(context.requested_metrics, ("retail_sales_index",))
        self.assertEqual(context.requested_period, "2028")
        self.assertEqual(context.requested_query, "일본 오프라인 니트 판매 지수 2028")
        self.assertEqual(context.inferred_scope["category"], "Knitwear")

    def test_http_context_does_not_invent_missing_region_or_channel(self):
        request = valid_request()
        request["input"]["planning_request"]["query"] = "아우터 시장 규모"
        for key in ("requested_category", "requested_geography", "requested_channel", "requested_metrics"):
            request["input"].pop(key, None)
        request["input"]["planning_request"].get("context", {}).pop("category", None)
        context = context_from_handoff_request(request)
        self.assertIsNone(context.requested_geography)
        self.assertIsNone(context.requested_channel)

    def test_explicit_scope_wins_and_conflict_is_retained_for_review(self):
        request = valid_request()
        request["input"]["planning_request"]["query"] = "outerwear purchase rows"
        request["input"]["requested_category"] = "Footwear"
        context = context_from_handoff_request(request)
        self.assertEqual(context.requested_category, "Footwear")
        self.assertEqual(context.inferred_scope["category"], "Outerwear")
        self.assertIn("explicit_category_overrides_query_hint", context.query_ambiguities)

    def test_http_configured_hm_source_obeys_permission_gate_without_reading_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = {
                "VOGUE_HM_PROCESSED_DIR": str(root / "does-not-exist"),
                "VOGUE_HM_CATEGORY_MAP": "Jacket=Outerwear", "VOGUE_HM_MAPPING_VERSION": "test-map-v1",
                "VOGUE_HM_USAGE_AUTHORIZED": "",
            }
            request = valid_request()
            request["input"]["planning_request"]["query"] = "Outerwear purchase rows"
            request["input"]["requested_metrics"] = ["purchase_rows"]
            request["input"].pop("requested_geography", None)
            request["input"].pop("requested_channel", None)
            with patch.dict(os.environ, settings):
                context = context_from_handoff_request(request)
                result = configured_agent().run(context)
            self.assertEqual(result.sales_data.status, "no_results")
            self.assertEqual(result.sales_data.missing_data[0].reason, "permission_not_confirmed")

    def post(self, request_body: dict | bytes, *, path="/v1/agents/sales-market-agent/runs", content_type="application/json", agent_factory=None, token="test-agent-token", synthetic_simulation_enabled=False):
        if agent_factory is None and synthetic_simulation_enabled:
            agent_factory = lambda: MarketResearchAgent(SyntheticQueryCoverageSource())
        handler_type = make_handler(agent_factory, synthetic_simulation_enabled=synthetic_simulation_enabled)
        handler = object.__new__(handler_type)
        data = request_body if isinstance(request_body, bytes) else json.dumps(request_body).encode()
        handler.path = path
        handler.headers = Message()
        handler.headers["Content-Type"] = content_type
        handler.headers["Content-Length"] = str(len(data))
        if token is not None:
            handler.headers["Authorization"] = f"Bearer {token}"
        handler.rfile = io.BytesIO(data)
        handler.wfile = io.BytesIO()
        handler.response_status = None
        handler.response_headers = {}
        handler.send_response = lambda status, *args: setattr(handler, "response_status", status)
        handler.send_header = lambda name, value: handler.response_headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        with patch.dict(os.environ, {"VOGUE_AGENT_SERVICE_BEARER_TOKEN": "test-agent-token"}):
            handler_type.do_POST(handler)
        return handler.response_status, json.loads(handler.wfile.getvalue()), handler.response_headers

    def get(self, *, path, synthetic_simulation_enabled=False):
        handler_type = make_handler(synthetic_simulation_enabled=synthetic_simulation_enabled)
        handler = object.__new__(handler_type)
        handler.path = path
        handler.headers = Message()
        handler.wfile = io.BytesIO()
        handler.response_status = None
        handler.response_headers = {}
        handler.send_response = lambda status, *args: setattr(handler, "response_status", status)
        handler.send_header = lambda name, value: handler.response_headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        handler_type.do_GET(handler)
        return handler.response_status, handler.wfile.getvalue(), handler.response_headers

    def test_canonical_routes_require_matching_shared_bearer_token(self):
        for token in (None, "wrong-token"):
            with self.subTest(token="missing" if token is None else "wrong"):
                status, response, _ = self.post(valid_request(), token=token)
                self.assertEqual(status, 401)
                self.assertEqual(response["error"]["code"], "UNAUTHORIZED")

    def test_contract_schema_and_mixed_version_fixture_match_v01(self):
        fixture = json.loads((ROOT / "examples/fixtures/http_json_handoff/cases.json").read_text(encoding="utf-8"))
        valid = next(item for item in fixture["cases"] if item["case_id"] == "ok_mixed_producer_versions")
        context = context_from_handoff_request(valid["request"])
        self.assertEqual(context.request_id, valid["request"]["request_id"])
        self.assertEqual(context.trend_result["schema_version"], "1.1")

        response_case = next(item for item in fixture["cases"] if item["case_id"] == "result_body_lineage_preserved")
        response = response_case["response"]
        self.assertEqual(set(response), RESPONSE_FIELDS)
        self.assertEqual(response["contract_version"], "vogue-handoff/0.1")
        self.assertEqual(response["producer"]["schema_version"], response["result"]["schema_version"])
        self.assertEqual(response["status"], response["result"]["status"])

        request_schema = json.loads((ROOT / "contracts/schemas/vogue-handoff-0.1-request.schema.json").read_text())
        response_schema = json.loads((ROOT / "contracts/schemas/vogue-handoff-0.1-response.schema.json").read_text())
        self.assertEqual(request_schema["required"], ["contract_version", "request_id", "input"])
        self.assertEqual(response_schema["required"], ["contract_version", "request_id", "producer", "status", "result"])
        invalid = next(item for item in fixture["cases"] if item["case_id"] == "transport_validation_error")
        with self.assertRaises(RequestValidationError):
            context_from_handoff_request(invalid["request"])

    def test_valid_synthetic_run_returns_exact_v01_envelope_and_default_result(self):
        status, response, _ = self.post(valid_request())
        self.assertEqual(status, 200)
        self.assertEqual(response["contract_version"], "vogue-handoff/0.1")
        self.assertEqual(response["request_id"], REQUEST_ID)
        self.assertEqual(response["producer"]["agent_id"], "sales-market-agent")
        self.assertEqual(response["producer"]["schema_version"], "1.0")
        self.assertEqual(response["status"], "no_results")
        self.assertEqual(response["result"]["status"], "no_results")
        self.assertEqual(response["result"]["request_id"], REQUEST_ID)
        self.assertEqual(response["result"]["schema_version"], "1.0")
        self.assertEqual(set(response), RESPONSE_FIELDS)

    def test_structured_run_accepts_only_canonical_request_and_keeps_v01_response(self):
        request = valid_structured_request()
        received = {}

        class FakeResult:
            def to_dict(self):
                return {
                    "schema_version": "1.0", "request_id": request["request_id"],
                    "agent_id": "sales-market-agent", "status": "no_results",
                    "summary": "No configured source.", "findings": [], "evidence": [],
                    "limitations": [], "errors": [],
                }

        class FakeAgent:
            def run(self, context):
                received["context"] = context
                return FakeResult()

        status, response, _ = self.post(
            request, path="/v1/agents/sales-market-agent/structured-runs", agent_factory=FakeAgent,
        )

        self.assertEqual(status, 200)
        self.assertEqual(set(response), RESPONSE_FIELDS)
        self.assertEqual(response["contract_version"], "vogue-handoff/0.1")
        context = received["context"]
        self.assertEqual(context.request_id, request["request_id"])
        self.assertEqual(context.requested_category, "Knitwear")
        self.assertEqual(context.requested_period, "2027-06-01..2027-08-31")
        self.assertIsNone(context.trend_result)
        self.assertIsNone(context.inventory_product_result)

    def test_structured_run_rejects_upstream_result_payloads(self):
        request = valid_structured_request()
        request["trend_result"] = {"request_id": request["request_id"], "status": "ok"}
        status, response, _ = self.post(
            request, path="/v1/agents/sales-market-agent/structured-runs",
        )
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "INVALID_CANONICAL_REQUEST")

    def test_opt_in_synthetic_demo_returns_labeled_analysis_without_changing_v01_envelope(self):
        request = valid_demo_request()
        status, response, _ = self.post(
            request,
            agent_factory=lambda: MarketResearchAgent(SyntheticQueryCoverageSource()),
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(response), RESPONSE_FIELDS)
        self.assertEqual(response["contract_version"], "vogue-handoff/0.1")
        self.assertEqual(response["producer"]["schema_version"], "1.0")
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["result"]["status"], "partial")
        self.assertEqual(response["result"]["request_id"], request["request_id"])
        self.assertEqual(len(response["result"]["findings"]), 2)
        self.assertTrue(all(item["kind"] == "proposal" for item in response["result"]["findings"]))
        self.assertTrue(all("[SYNTHETIC TEST DATA]" in item["statement"] for item in response["result"]["findings"]))
        self.assertTrue(all("excluded colors=black" in item["statement"] for item in response["result"]["findings"]))
        self.assertTrue(any("not actual H&M/VOGUE sales" in item for item in response["result"]["limitations"]))
        self.assertEqual(response["result"]["evidence"], [])

    def test_synthetic_demo_natural_language_without_supported_scope_still_searches_fixture(self):
        request = valid_demo_request()
        request["input"]["requested_query"] = "시장 데이터를 요약해줘"
        request["input"].pop("requested_category", None)
        request["input"].pop("requested_period", None)
        # Keep gender unspecified too; unrequested dimensions must not become hard filters.
        request["input"]["planning_request"]["context"].pop("gender", None)
        status, response, _ = self.post(
            request,
            agent_factory=lambda: MarketResearchAgent(SyntheticQueryCoverageSource()),
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["status"], "partial")
        self.assertGreater(len(response["result"]["findings"]), 0)
        self.assertTrue(all("[SYNTHETIC TEST DATA]" in item["statement"] for item in response["result"]["findings"]))

    def test_synthetic_stable_route_filters_to_the_requested_metric(self):
        request = valid_demo_request()
        request["input"]["requested_metrics"] = ["scenario_units_sold"]
        status, response, _ = self.post(request, synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        findings = response["result"]["findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["topic"], "synthetic_sales_demo")
        self.assertIn("item_units", findings[0]["statement"])

    def test_synthetic_simulation_api_is_separate_opt_in_and_keeps_safety_lineage(self):
        request = json.loads((ROOT / "examples/synthetic_rag_analysis/women-knitwear-2028-03.request.json").read_text(encoding="utf-8"))
        status, disabled, _ = self.post(request, path=SYNTHETIC_SIMULATION_PATH)
        self.assertEqual(status, 404)
        self.assertEqual(disabled["error"]["code"], "NOT_FOUND")

        status, response, headers = self.post(
            request, path=SYNTHETIC_SIMULATION_PATH, synthetic_simulation_enabled=True,
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["schema_version"], "synthetic-rag-analysis/0.1-proposal")
        self.assertEqual(response["simulation_mode"], "synthetic_simulation")
        self.assertTrue(response["synthetic"])
        self.assertEqual(response["data_nature"], "synthetic_test")
        self.assertEqual(response["permission_status"], "authorized_for_synthetic_testing_only")
        self.assertFalse(response["operational_eligible"])
        self.assertEqual(response["requested_scope"]["raw_query"], request["raw_query"])
        self.assertEqual(len(response["market_like"]["observations"]), 6)
        self.assertEqual(response["sales_like"]["status"], "not_queried")
        for observation in response["market_like"]["observations"]:
            self.assertTrue(observation["observation_id"])
            self.assertTrue(observation["synthetic"])
            self.assertTrue(observation["evidence_ids"])
            self.assertTrue(all(item["locator"] and item["content_hash"] for item in observation["evidence"]))
        self.assertEqual(headers["Cache-Control"], "no-store")

        # Explicit simulation service mode also supplies synthetic analysis through the stable handoff route.
        status, regular, headers = self.post(valid_demo_request(), synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        self.assertEqual(regular["contract_version"], "vogue-handoff/0.1")
        self.assertIn(regular["status"], {"ok", "partial"})
        self.assertTrue(any("[SYNTHETIC TEST DATA]" in item["statement"] for item in regular["result"]["findings"]))
        self.assertEqual(regular["result"]["evidence"], [])
        self.assertEqual(headers["X-Data-Nature"], "synthetic_test")
        self.assertEqual(headers["X-Operational-Eligible"], "false")

    def test_synthetic_simulation_route_requires_existing_bearer_auth_and_valid_canonical_input(self):
        request = json.loads((ROOT / "examples/synthetic_rag_analysis/women-knitwear-2028-03.request.json").read_text(encoding="utf-8"))
        for token in (None, "wrong-token"):
            status, response, _ = self.post(
                request, path=SYNTHETIC_SIMULATION_PATH, token=token,
                synthetic_simulation_enabled=True,
            )
            self.assertEqual(status, 401)
            self.assertEqual(response["reason"], "unauthorized")
            self.assertTrue(response["synthetic"])
        invalid = dict(request)
        invalid["trend_result"] = {"status": "ok"}
        status, response, _ = self.post(
            invalid, path=SYNTHETIC_SIMULATION_PATH, synthetic_simulation_enabled=True,
        )
        self.assertEqual(status, 422)
        self.assertEqual(response["reason"], "invalid_canonical_request")
        self.assertFalse(response["operational_eligible"])

    def test_synthetic_simulation_fixture_failure_is_safe_source_error(self):
        from market_research_agent import http_server
        request = json.loads((ROOT / "examples/synthetic_rag_analysis/women-knitwear-2028-03.request.json").read_text(encoding="utf-8"))
        with patch.object(http_server, "analyze_synthetic_request", side_effect=http_server.SyntheticFixtureError("sensitive-path-marker")):
            status, response, _ = self.post(
                request, path=SYNTHETIC_SIMULATION_PATH, synthetic_simulation_enabled=True,
            )
        self.assertEqual(status, 503)
        self.assertEqual(response["reason"], "fixture_unavailable_or_invalid")
        self.assertNotIn("sensitive-path-marker", json.dumps(response))
        self.assertTrue(response["synthetic"])

    def test_synthetic_simulation_ui_is_opt_in_and_has_strong_disclosure(self):
        status, _, _ = self.get(path="/")
        self.assertEqual(status, 404)
        status, _, _ = self.get(path=SYNTHETIC_SIMULATION_UI_PATH)
        self.assertEqual(status, 404)
        status, page, headers = self.get(path=SYNTHETIC_SIMULATION_UI_PATH, synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        self.assertIn("합성 데이터 시뮬레이션".encode(), page)
        self.assertIn("실제 시장 관측".encode(), page)
        self.assertIn(b"/synthetic-simulation.js", page)
        self.assertIn("default-src 'none'" , headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
        status, script, _ = self.get(path="/synthetic-simulation.js", synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        self.assertIn(b"textContent", script)
        self.assertNotIn(b"localStorage", script)

    def test_simulation_is_available_from_main_app_entry_and_agent_api_namespace(self):
        request = json.loads((ROOT / "examples/synthetic_rag_analysis/women-knitwear-2028-03.request.json").read_text(encoding="utf-8"))
        status, page, _ = self.get(path="/", synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        self.assertIn("합성 데이터 시뮬레이션".encode(), page)
        status, script, _ = self.get(path="/synthetic-simulation.js", synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        self.assertIn(b"/v1/agents/sales-market-agent/runs", script)

        status, payload, _ = self.post(request, path=AGENT_SIMULATION_PATH, synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        self.assertEqual(payload["simulation_mode"], "synthetic_simulation")
        self.assertTrue(payload["synthetic"])
        status, health_body, _ = self.get(path="/api/health", synthetic_simulation_enabled=True)
        self.assertEqual(status, 200)
        health = json.loads(health_body)
        self.assertEqual(health["mode"], "synthetic_simulation")
        self.assertFalse(health["operational_eligible"])

    def test_simulation_cli_flag_is_opt_in_and_cannot_mix_with_legacy_demo(self):
        with patch.object(http_server, "serve") as serve:
            self.assertEqual(http_server.main([]), 0)
            serve.assert_called_once_with("127.0.0.1", 8766, synthetic_demo=False, enable_synthetic_simulation=False)
            serve.reset_mock()
            self.assertEqual(http_server.main(["--enable-synthetic-simulation"]), 0)
            serve.assert_called_once_with("127.0.0.1", 8766, synthetic_demo=False, enable_synthetic_simulation=True)
        with self.assertRaises(ValueError):
            http_server.serve(synthetic_demo=True, enable_synthetic_simulation=True)
        with self.assertRaises(ValueError):
            http_server.serve(host="0.0.0.0", enable_synthetic_simulation=True)

    def test_project08_route_alias_is_supported_for_local_proposal_client(self):
        status, response, headers = self.post(
            valid_request(), path="/v1/agents/market-research-agent/runs",
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["producer"]["agent_id"], "sales-market-agent")
        self.assertEqual(headers["Deprecation"], "true")

    def test_all_domain_statuses_use_http_200_and_preserve_result_body(self):
        for domain_status in ("ok", "partial", "no_results", "error"):
            with self.subTest(status=domain_status):
                raw_result = {
                    "schema_version": "1.0",
                    "request_id": REQUEST_ID,
                    "agent_id": "sales-market-agent",
                    "status": domain_status,
                    "summary": "synthetic exact body",
                    "findings": [], "evidence": [], "limitations": ["synthetic"], "errors": [],
                }

                class FakeResult:
                    def to_dict(self):
                        return raw_result

                class FakeAgent:
                    def run(self, context):
                        self.context = context
                        return FakeResult()

                status, response, _ = self.post(
                    valid_request(), agent_factory=FakeAgent,
                )
                self.assertEqual(status, 200)
                self.assertEqual(response["status"], domain_status)
                self.assertEqual(response["result"]["status"], domain_status)
                self.assertEqual(response["result"], raw_result)
                self.assertEqual(set(response), RESPONSE_FIELDS)

    def test_service_failures_use_safe_transport_error_envelope(self):
        class FailingAgent:
            def run(self, context):
                raise RuntimeError("synthetic-secret-marker")

        status, response, _ = self.post(valid_request(), agent_factory=FailingAgent)
        self.assertEqual(status, 500)
        self.assertEqual(response["contract_version"], "vogue-handoff/0.1")
        self.assertEqual(response["request_id"], REQUEST_ID)
        self.assertEqual(response["error"]["code"], "RUN_FAILED")
        self.assertNotIn("synthetic-secret-marker", json.dumps(response))

        class UnavailableAgent:
            def run(self, context):
                raise ConnectionError("synthetic-endpoint-marker")

        status, response, _ = self.post(valid_request(), agent_factory=UnavailableAgent)
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertNotIn("synthetic-endpoint-marker", json.dumps(response))

    def test_request_id_mismatch_is_rejected_before_agent_run(self):
        mutations = (
            lambda item: item.update(request_id="synthetic-different-id"),
            lambda item: item["input"].update(request_id="synthetic-different-id"),
            lambda item: item["input"]["planning_request"].update(request_id="synthetic-different-id"),
            lambda item: item["input"]["trend_result"].update(request_id="synthetic-different-id"),
            lambda item: item["input"]["inventory_product_result"].update(request_id="synthetic-different-id"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                request = valid_request()
                mutate(request)
                status, response, _ = self.post(request)
                self.assertEqual(status, 422)
                self.assertEqual(response["error"]["code"], "REQUEST_ID_MISMATCH")

        request = valid_request()
        request["input"]["trend_result"]["result"] = {"request_id": "synthetic-different-id"}
        status, response, _ = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "REQUEST_ID_MISMATCH")

    def test_unsupported_contract_and_planning_schema_are_rejected(self):
        request = valid_request()
        request["contract_version"] = "unsupported"
        status, response, _ = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_CONTRACT_VERSION")

        request = valid_request()
        request["input"]["planning_request"]["schema_version"] = "99.0"
        status, response, _ = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_PRODUCER_SCHEMA")

        request = valid_request()
        request["input"]["trend_result"]["schema_version"] = "99.0"
        status, response, _ = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_PRODUCER_SCHEMA")

        request = valid_request()
        request["input"]["inventory_product_result"]["schema_version"] = "99.0"
        status, response, _ = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_PRODUCER_SCHEMA")

    def test_invalid_upstream_status_and_oversized_body_are_rejected(self):
        request = valid_request()
        request["input"]["trend_result"]["status"] = "unknown"
        status, response, _ = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "INVALID_UPSTREAM_STATUS")

        request = valid_request()
        request["input"]["uncontracted_field"] = "synthetic"
        status, response, _ = self.post(request)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")

        status, response, _ = self.post(b" " * (MAX_BODY_BYTES + 1))
        self.assertEqual(status, 413)
        self.assertEqual(response["error"]["code"], "REQUEST_TOO_LARGE")

    def test_malformed_json_media_type_and_wrong_route_are_rejected(self):
        status, response, _ = self.post(b"{bad", content_type="application/json")
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "INVALID_JSON")

        status, response, _ = self.post(valid_request(), content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")

        status, response, _ = self.post(valid_request(), path="/v1/agents/other-agent/runs")
        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["code"], "AGENT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
