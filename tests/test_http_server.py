"""HTTP tests for the minimal product review API."""

from __future__ import annotations

import io
import json
import os
import unittest
from email.message import Message
from unittest.mock import patch

from market_research_agent.http_server import REVIEW_CONTRACT_VERSION, _validate_review_result, make_handler


class Project07ReviewHttpTests(unittest.TestCase):
    def request(
        self, method: str, path: str, payload=None, *, token="test-token",
        research_bundle=None, env_overrides=None, fx_reference=None, llm_proposal=None,
        llm_failure=None,
    ):
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
        research_bundle = research_bundle or {
            "fabric_prices": (), "fabric_price_error": None,
            "fabric_price_source_status": "not_configured",
            "customer_age_profile": None, "customer_age_error": None,
            "historical_price": None, "historical_price_error": None,
        }
        test_env = {
            "VOGUE_AGENT_SERVICE_BEARER_TOKEN": "test-token",
            "VOGUE_PROJECT07_DISABLE_DOTENV": "true",
            "VOGUE_HM_RESEARCH_NONCOMMERCIAL": "false",
            "VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED": "false",
        }
        test_env.update(env_overrides or {})
        with (
            patch.dict(os.environ, test_env),
            patch("market_research_agent.http_server.HmCombinedResearchSource.analyze", return_value=research_bundle),
            patch("market_research_agent.http_server.fetch_usd_krw_reference_rate", return_value=fx_reference),
            patch("market_research_agent.http_server.propose_target_audience",
                  side_effect=llm_failure if llm_failure is not None else None,
                  return_value=llm_proposal),
        ):
            (handler_type.do_POST if method == "POST" else handler_type.do_GET)(handler)
        output = handler.wfile.getvalue()
        return handler.response_status, json.loads(output) if output else None

    def test_disconnected_client_does_not_trigger_second_response(self):
        handler_type = make_handler()
        handler = object.__new__(handler_type)
        handler.send_response = lambda _status: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None

        class DisconnectedWriter:
            def write(self, _value):
                raise BrokenPipeError("client disconnected")

        handler.wfile = DisconnectedWriter()
        handler._send_json(200, {"status": "ok"})

    def valid_review(self):
        product = {
            "category": "Outerwear", "product_type": "Jacket",
            "gender": "women", "season": "winter", "material": "wool blend",
        }
        return {
            "contract_version": REVIEW_CONTRACT_VERSION,
            "request_id": "review-http-001",
            "input": {
                "request_id": "review-http-001",
                "product": product,
                "product_evidence": {
                    field: {
                        "kind": "user_requested", "source_ref": "request.product." + field,
                        "evidence_ids": [],
                    }
                    for field in product
                },
            },
        }

    def test_review_route_returns_review_and_human_decision_support(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["contract_version"], REVIEW_CONTRACT_VERSION)
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

    def test_no_sources_returns_no_results_not_empty_partial(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "no_results")
        self.assertEqual(response["result"]["status"], "no_results")
        self.assertEqual(response["status"], response["result"]["status"])
        self.assertEqual(response["result"]["category_price_comparison"], [])
        self.assertEqual(response["result"]["material_cost_analysis"], [])
        self.assertEqual(response["result"]["customer_segment_analysis"], [])

    def test_age_failure_retains_successful_results_at_native_contract_paths(self):
        prices = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only",
            "sample_count": 24,
            "price_statistics": {"mean": 0.02, "median": 0.02},
            "scaled_price_statistics": {"mean": 11.8, "currency": "USD", "unit": "USD"},
            "sources": [{"object_key": "transactions.parquet", "version": "synthetic-v1"}],
        }
        fabric = ({
            "source_type": "owner_provided_public_fabric_listing_snapshot",
            "source_grade": "public_sale",
            "source_object": {"object_key": "fabric.csv", "version": "synthetic-v1"},
            "sources": [],
            "permission_status": "owner_provided_snapshot",
        },)
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
            research_bundle={
                "fabric_prices": fabric, "fabric_price_error": None,
                "fabric_price_source_status": "available",
                "customer_age_profile": None,
                "customer_age_error": "synthetic DuckDB timeout",
                "historical_price": prices, "historical_price_error": None,
            },
        )
        review = response["result"]
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "partial")
        self.assertEqual(review["status"], response["status"])
        self.assertEqual(review["request_id"], response["request_id"])
        self.assertEqual(review["category_price_comparison"][0]["sample_count"], 24)
        self.assertEqual(review["material_cost_analysis"][0]["source_object"], fabric[0]["source_object"])
        self.assertTrue(review["material_cost_analysis"][0]["evidence_ids"])
        evidence_by_id = {item["evidence_id"]: item for item in review["evidence"]}
        for evidence_id in review["category_price_comparison"][0]["evidence_ids"]:
            self.assertIn("result.category_price_comparison", evidence_by_id[evidence_id]["supports_fields"])
        self.assertEqual({item["code"] for item in review["errors"]}, {"CUSTOMER_AGE_RESEARCH_FAILED"})
        self.assertGreaterEqual(len(review["evidence"]), 2)
        # P08's agreed handoff consumes the native P07 result under `result`.
        consumer_result = response["result"]
        self.assertTrue(consumer_result["category_price_comparison"])
        self.assertTrue(consumer_result["material_cost_analysis"])
        self.assertTrue(consumer_result["missing_data"])
        _validate_review_result(consumer_result, response["request_id"])

    def test_all_documented_output_fields_survive_repeated_local_responses(self):
        """Exercise the documented output surface using synthetic source bundles."""
        prices = {
            "source_id": "synthetic-hm-research",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only",
            "sample_count": 24,
            "price_statistics": {"mean": 0.02, "median": 0.018, "p25": 0.01, "p75": 0.03},
            "scaled_price_statistics": {"mean": 11.8, "p25": 5.9, "p75": 17.7, "currency": "USD", "unit": "USD"},
            "period": {"start": "2020-01-01", "end": "2020-12-31"},
            "sources": [{"object_key": "synthetic/transactions.parquet", "version": "fixture-v1"}],
        }
        fabric = ({
            "source_type": "owner_provided_public_fabric_listing_snapshot",
            "source_grade": "public_sale",
            "source_object": {"object_key": "synthetic/fabric.csv", "version": "fixture-v1"},
            "sources": [{"record_id": "fixture-fabric-1", "unit_price_krw_per_meter": 12000}],
            "permission_status": "owner_provided_snapshot",
            "verified_price_per_meter_available": False,
        },)
        profile = {
            "source_id": "synthetic-hm-research",
            "source_type": "historical_kaggle_customer_article_transaction_aggregate",
            "permission_status": "non_commercial_research_only",
            "target_age_cohort": {"age_band": "20_39", "suppressed": False},
            "age_bands": [{"age_band": "20_29", "unique_customer_count": 12}],
            "sources": [{"object_key": "synthetic/customers.parquet", "version": "fixture-v1"}],
        }
        bundle = {
            "fabric_prices": fabric, "fabric_price_error": None,
            "fabric_price_source_status": "available",
            "customer_age_profile": profile, "customer_age_error": None,
            "customer_age_source_status": "available",
            "historical_price": prices, "historical_price_error": None,
            "historical_price_source_status": "available",
        }
        fx = {
            "base_currency": "USD", "quote_currency": "KRW", "rate": 1400.0,
            "rate_date": "2026-11-13", "provider": "synthetic-fixture",
            "rate_type": "reference", "fallback_used": False,
            "fallback_age_days": 0, "stale": False,
        }
        proposal = {"primary_segment": {"description": "합성 검증용 타깃 가설"}}
        documented_fields = (
            "category_price_comparison", "material_cost_analysis", "customer_segment_analysis",
            "target_audience_analysis", "evidence", "missing_data", "errors", "limitations",
            "margin_estimate", "margin_price_range_analysis", "decision_support",
        )

        for attempt in range(5):
            with self.subTest(attempt=attempt + 1):
                request = self.valid_review()
                request_id = f"review-http-repeat-{attempt + 1}"
                request["request_id"] = request_id
                request["input"]["request_id"] = request_id
                status, response = self.request(
                    "POST", "/v1/agents/sales-market-agent/reviews", request,
                    research_bundle=bundle,
                    env_overrides={
                        "VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true",
                        "VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED": "true",
                        "LLM_API_KEY": "synthetic-test-key",
                        "LLM_MODEL": "synthetic-test-model",
                    },
                    fx_reference=fx,
                    llm_proposal=proposal,
                )

                self.assertEqual(status, 200)
                self.assertEqual(response["status"], "ok")
                result = response["result"]
                self.assertEqual(result["status"], response["status"])
                self.assertEqual(result["request_id"], request_id)
                self.assertTrue(set(documented_fields).issubset(result))
                self.assertTrue(result["category_price_comparison"])
                self.assertEqual(result["category_price_comparison"][0]["sample_count"], 24)
                self.assertTrue(result["material_cost_analysis"])
                self.assertEqual(result["material_cost_analysis"][0]["sources"][0]["record_id"], "fixture-fabric-1")
                self.assertTrue(result["customer_segment_analysis"])
                self.assertEqual(result["target_audience_analysis"]["age_cohort"], profile["target_age_cohort"])
                self.assertEqual(result["target_audience_analysis"]["proposal"], proposal)
                self.assertGreaterEqual(len(result["evidence"]), 3)
                self.assertEqual(result["missing_data"], [])
                self.assertEqual(result["errors"], [])
                self.assertTrue(result["limitations"])
                self.assertEqual(result["margin_estimate"]["status"], "benchmark_estimate")
                self.assertEqual(
                    result["margin_estimate"]["estimate_type"],
                    "industry_average_gross_margin_benchmark",
                )
                self.assertEqual(result["margin_price_range_analysis"]["status"], "benchmark_estimate")
                self.assertEqual(result["margin_price_range_analysis"]["fx_provider"], "synthetic-fixture")
                self.assertEqual(result["decision_support"]["recommendation"], "review_required")
                self.assertEqual(result["decision_support"]["evidence_areas"]["category_price"], True)
                self.assertEqual(result["decision_support"]["evidence_areas"]["customer_segments"], True)
                evidence_by_id = {item["evidence_id"]: item for item in result["evidence"]}
                self.assertEqual(len(evidence_by_id), len(result["evidence"]))
                self.assertTrue(all(item["analysis"].get("observed") for item in result["evidence"]))
                price_evidence = evidence_by_id[result["category_price_comparison"][0]["evidence_ids"][0]]
                customer_evidence = evidence_by_id[result["customer_segment_analysis"][0]["evidence_ids"][0]]
                fabric_evidence = evidence_by_id[result["material_cost_analysis"][0]["evidence_ids"][0]]
                self.assertEqual(price_evidence["analysis"]["observed"]["sample_count"], 24)
                self.assertEqual(customer_evidence["analysis"]["observed"]["target_age_cohort"], profile["target_age_cohort"])
                self.assertEqual(fabric_evidence["analysis"]["observed"]["matched_record_ids"], ["fixture-fabric-1"])
                benchmark_evidence = next(item for item in result["evidence"] if item["source_type"] == "industry_gross_margin_benchmark")
                fx_evidence = next(item for item in result["evidence"] if item["source_type"] == "usd_krw_reference_rate")
                self.assertEqual(benchmark_evidence["analysis"]["observed"]["gross_margin_rate"], 0.5688)
                self.assertEqual(fx_evidence["analysis"]["observed"]["rate_date"], "2026-11-13")
                for rows, path in (
                    (result["category_price_comparison"], "result.category_price_comparison"),
                    (result["material_cost_analysis"], "result.material_cost_analysis"),
                    (result["customer_segment_analysis"], "result.customer_segment_analysis"),
                ):
                    for row in rows:
                        self.assertTrue(row["evidence_ids"])
                        for evidence_id in row["evidence_ids"]:
                            self.assertIn(path, evidence_by_id[evidence_id]["supports_fields"])
                self.assertTrue(result["target_audience_analysis"]["evidence_ids"])
                self.assertTrue(result["margin_estimate"]["evidence_ids"])
                self.assertTrue(result["price_analysis"]["evidence_ids"])
                self.assertTrue(result["fabric_cost_analysis"]["evidence_ids"])
                self.assertTrue(result["margin_analysis"]["evidence_ids"])
                self.assertTrue(result["margin_price_range_analysis"]["evidence_ids"])
                _validate_review_result(result, request_id)

    def test_optional_llm_failure_marks_successful_research_as_partial(self):
        from market_research_agent.llm.responses_api import LlmCallError

        prices = {
            "source_id": "synthetic-hm-research",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only",
            "sample_count": 12,
            "price_statistics": {"mean": 0.02},
            "sources": [{"object_key": "synthetic/transactions.parquet", "version": "fixture-v1"}],
        }
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
            research_bundle={
                "fabric_prices": (), "fabric_price_error": None,
                "fabric_price_source_status": "not_configured",
                "customer_age_profile": None, "customer_age_error": None,
                "customer_age_source_status": "disabled",
                "historical_price": prices, "historical_price_error": None,
                "historical_price_source_status": "available",
            },
            env_overrides={
                "VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true",
                "VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED": "true",
                "LLM_API_KEY": "synthetic-test-key",
                "LLM_MODEL": "synthetic-test-model",
            },
            fx_reference={
                "base_currency": "USD", "quote_currency": "KRW", "rate": 1400.0,
                "rate_date": "2026-11-13", "provider": "synthetic-fixture",
                "rate_type": "reference", "fallback_used": False,
            },
            llm_failure=LlmCallError("LLM provider returned HTTP 401."),
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["status"], response["result"]["status"])
        self.assertEqual(response["result"]["target_audience"]["llm_call_status"], "failed")
        self.assertIn("TARGET_AUDIENCE_LLM_FAILED", {item["code"] for item in response["result"]["errors"]})

    def test_fabric_failure_does_not_drop_price_or_customer_analysis(self):
        profile = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_customer_article_transaction_aggregate",
            "permission_status": "non_commercial_research_only",
            "sources": [{"object_key": "customers.csv", "version": "synthetic-v1"}],
        }
        prices = {
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only", "sample_count": 13,
            "price_statistics": {"mean": 0.01},
            "sources": [{"object_key": "transactions.parquet", "version": "synthetic-v1"}],
        }
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
            research_bundle={
                "fabric_prices": (), "fabric_price_error": "synthetic access denied",
                "fabric_price_source_status": "error",
                "customer_age_profile": profile, "customer_age_error": None,
                "historical_price": prices, "historical_price_error": None,
            },
        )
        review = response["result"]
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "partial")
        self.assertTrue(review["category_price_comparison"])
        self.assertTrue(review["customer_segment_analysis"])
        self.assertIn("R2_FABRIC_PRICE_READ_FAILED", {item["code"] for item in review["errors"]})

    def test_review_contract_validator_rejects_missing_fields_and_status_mismatch(self):
        with self.assertRaisesRegex(ValueError, "missing contract fields"):
            _validate_review_result({}, "review-http-001")
        _status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
        )
        invalid = dict(response["result"], status="partial")
        with self.assertRaisesRegex(ValueError, "status claims results"):
            _validate_review_result(invalid, response["request_id"])
        inconsistent = dict(response["result"])
        inconsistent["category_price_comparison"] = [{"sample_count": 1}]
        with self.assertRaisesRegex(ValueError, "no_results but review content"):
            _validate_review_result(inconsistent, response["request_id"])

    def test_r2_fabric_read_failure_is_nonfatal_and_preserves_request_id(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
            research_bundle={
                "fabric_prices": (), "fabric_price_error": "synthetic snapshot unavailable",
                "fabric_price_source_status": "error",
                "customer_age_profile": None, "customer_age_error": None,
                "historical_price": None, "historical_price_error": None,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(response["request_id"], "review-http-001")
        self.assertEqual(response["result"]["request_id"], "review-http-001")
        self.assertEqual(response["status"], "no_results")
        self.assertIn(
            "R2_FABRIC_PRICE_READ_FAILED",
            {error["code"] for error in response["result"]["errors"]},
        )
        self.assertEqual(response["result"]["material_cost_analysis"], [])
        self.assertEqual(response["result"]["fabric_cost_analysis"]["unit_cost"], None)
        self.assertEqual(response["result"]["missing_data"][1]["reason"], "source_error")

    def test_customer_age_failure_is_partial_and_keeps_review_linkage(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
            research_bundle={
                "fabric_prices": (), "fabric_price_error": None,
                "fabric_price_source_status": "not_configured",
                "customer_age_profile": None,
                "customer_age_error": "H&M research aggregates could not be read safely from R2 (TimeoutError).",
                "historical_price": {"category": "Outerwear", "sample_count": 20},
                "historical_price_error": None,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["request_id"], "review-http-001")
        self.assertEqual(response["result"]["request_id"], "review-http-001")
        self.assertEqual(response["status"], "partial")
        errors = {error["code"]: error["message"] for error in response["result"]["errors"]}
        self.assertIn("CUSTOMER_AGE_RESEARCH_FAILED", errors)
        self.assertIn("TimeoutError", errors["CUSTOMER_AGE_RESEARCH_FAILED"])
        self.assertTrue(response["result"]["category_price_comparison"])
        self.assertEqual(response["result"]["price_analysis"]["status"], "reference_only")
        self.assertEqual(response["result"]["draft_context"]["category"], "Outerwear")

    def test_integrated_case_keeps_price_when_age_fails_and_fabric_has_no_match(self):
        historical_price = {
            "source_id": "synthetic-hm-research",
            "source_type": "historical_kaggle_transaction_price_aggregate",
            "permission_status": "non_commercial_research_only",
            "sample_count": 3_163_443,
            "price_statistics": {"mean": 0.0278, "median": 0.025, "p25": 0.018, "p75": 0.034},
            "scaled_price_statistics": {"mean": 16.402, "median": 14.75, "p25": 10.62, "p75": 20.06,
                                         "scale_factor": 590, "currency": "USD", "unit": "USD"},
            "period": {"start": "2018-09-20", "end": "2020-09-22"},
            "sources": [{"object_key": "synthetic/transactions.parquet", "version": "fixture-v1"}],
        }
        response_status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
            research_bundle={
                "fabric_prices": (), "fabric_price_error": None,
                "fabric_price_source_status": "no_match",
                "customer_age_profile": None,
                "customer_age_error": "H&M research aggregates could not be read safely from R2 (stage=object_metadata; code=ObjectNotFound).",
                "customer_age_source_status": "error",
                "historical_price": historical_price, "historical_price_error": None,
                "historical_price_source_status": "available",
            },
            env_overrides={"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true"},
            fx_reference={
                "base_currency": "USD", "quote_currency": "KRW", "rate": 1400.0,
                "rate_date": "2026-11-13", "provider": "synthetic-fixture",
                "rate_type": "reference", "fallback_used": False, "fallback_age_days": 0,
                "stale": False,
            },
        )

        self.assertEqual(response_status, 200)
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["status"], response["result"]["status"])
        self.assertEqual(response["request_id"], response["result"]["request_id"])
        result = response["result"]
        self.assertEqual(result["price_analysis"]["status"], "ok")
        self.assertEqual(result["price_analysis"]["proposed_price"], 23_000)
        self.assertEqual(result["price_analysis"]["sample_size"], 3_163_443)
        self.assertEqual(result["price_analysis"]["period"], historical_price["period"])
        self.assertTrue(result["category_price_comparison"])
        self.assertEqual(result["customer_segment_analysis"], [])
        self.assertIsNone(result["target_audience_analysis"]["age_cohort"])
        self.assertEqual(result["fabric_cost_analysis"]["status"], "no_results")
        self.assertIsNone(result["fabric_cost_analysis"]["unit_cost"])
        missing = {item["field"]: item for item in result["missing_data"]}
        self.assertEqual(missing["fabric_costs"]["reason"], "no_match")
        self.assertEqual(missing["customer_segments"]["reason"], "source_error")
        age_error = next(item for item in result["errors"] if item["code"] == "CUSTOMER_AGE_RESEARCH_FAILED")
        self.assertIn("stage=object_metadata", age_error["message"])
        self.assertIn("code=ObjectNotFound", age_error["message"])
        _validate_review_result(result, response["request_id"])

    def test_unconfigured_fabric_source_returns_missing_data_without_invented_cost(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
        )

        self.assertEqual(status, 200)
        self.assertEqual(response["request_id"], "review-http-001")
        self.assertEqual(response["result"]["request_id"], response["request_id"])
        errors = {error["code"] for error in response["result"]["errors"]}
        self.assertNotIn("R2_FABRIC_PRICE_READ_FAILED", errors)
        self.assertEqual(response["result"]["material_cost_analysis"], [])
        self.assertIsNone(response["result"]["fabric_cost_analysis"]["unit_cost"])
        self.assertEqual(response["result"]["missing_data"][1]["field"], "fabric_costs")
        self.assertEqual(
            response["result"]["missing_data"][1]["reason"],
            "source_not_configured",
        )
        self.assertEqual(
            response["result"]["missing_data"][1]["message"],
            "R2 fabric price source is not configured.",
        )

    def test_configured_fabric_source_without_material_match_is_not_a_read_error(self):
        status, response = self.request(
            "POST", "/v1/agents/sales-market-agent/reviews", self.valid_review(),
            research_bundle={
                "fabric_prices": (), "fabric_price_error": None,
                "fabric_price_source_status": "no_match",
                "customer_age_profile": None, "customer_age_error": None,
                "historical_price": None, "historical_price_error": None,
            },
        )

        self.assertEqual(status, 200)
        result = response["result"]
        self.assertEqual(response["status"], "no_results")
        self.assertNotIn("R2_FABRIC_PRICE_READ_FAILED", {error["code"] for error in result["errors"]})
        fabric_missing = next(item for item in result["missing_data"] if item["field"] == "fabric_costs")
        self.assertEqual(fabric_missing["reason"], "no_match")
        self.assertEqual(result["material_cost_analysis"], [])
        self.assertEqual(result["fabric_cost_analysis"]["status"], "no_results")
        self.assertIsNone(result["fabric_cost_analysis"]["unit_cost"])

    def test_category_only_minimum_product_request_is_accepted(self):
        payload = self.valid_review()
        payload["input"]["product"] = {"category": "Outerwear"}
        payload["input"]["product_evidence"] = {
            "category": {
                "kind": "user_requested", "source_ref": "request.product.category",
                "evidence_ids": [],
            },
        }
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
        self.assertEqual(response["contract_version"], REVIEW_CONTRACT_VERSION)

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
        self.assertEqual(response["contract_version"], REVIEW_CONTRACT_VERSION)
        self.assertIsNone(response["request_id"])
        self.assertEqual(response["error"]["code"], "UNAUTHORIZED")
        self.assertTrue(response["error"]["message"])

    def test_retired_02_contract_is_rejected_explicitly(self):
        payload = self.valid_review()
        payload["contract_version"] = "vogue-sales-market-review/0.2-proposal"
        status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
        self.assertEqual(status, 422)
        self.assertEqual(response["contract_version"], REVIEW_CONTRACT_VERSION)
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_CONTRACT_VERSION")

    def test_current_contract_requires_provenance_for_each_product_field(self):
        payload = self.valid_review()
        del payload["input"]["product_evidence"]["material"]
        status, response = self.request("POST", "/v1/agents/sales-market-agent/reviews", payload)
        self.assertEqual(status, 422)
        self.assertEqual(response["error"]["code"], "INVALID_PRODUCT_EVIDENCE")

    def test_retired_research_endpoints_are_not_available(self):
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
