from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from market_research_agent import MarketResearchAgent, MarketResearchContext, ResultStatus, SourceReport


FIXTURE = Path(__file__).parent / "fixtures" / "upstream-envelope.synthetic.json"


class CapturingSource:
    def __init__(self, *, mutate: bool = False):
        self.received = None
        self.mutate = mutate

    def research(self, context):
        self.received = context
        if self.mutate:
            context.trend_result["producer_extension"]["raw"] = "mutated-by-adapter"
        return SourceReport(status=ResultStatus.NO_RESULTS)


class UpstreamContextTests(unittest.TestCase):
    def _fixture_inputs(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertTrue(payload["synthetic_fixture"])
        return payload

    def test_opaque_upstream_payload_reaches_source_and_proposal_review_unchanged(self):
        fixture = self._fixture_inputs()
        expected = {name: copy.deepcopy(fixture[name]) for name in (
            "planning_request", "trend_result", "inventory_product_result",
        )}
        context = MarketResearchContext(
            request_id=fixture["request_id"],
            planning_request=fixture["planning_request"],
            trend_result=fixture["trend_result"],
            inventory_product_result=fixture["inventory_product_result"],
        )
        source = CapturingSource(mutate=True)
        result = MarketResearchAgent(source).run(context)

        self.assertIs(source.received, context)
        self.assertIs(source.received.trend_result, context.trend_result)
        self.assertEqual(result.upstream_context, expected)
        self.assertEqual(result.status, ResultStatus.NO_RESULTS)
        self.assertEqual(result.to_dict()["schema_version"], "1.0")
        self.assertNotIn("upstream_context", result.to_dict())
        self.assertNotIn("upstream_context", result.to_proposal_dict())
        self.assertEqual(result.to_proposal_v1_2_dict()["upstream_context"], expected)
        self.assertEqual(result.to_proposal_v1_3_dict()["upstream_context"], expected)
        self.assertEqual(result.schema_version, "1.0")
        # Inventory is context only; it did not become sales, demand, price, or margin.
        proposal = result.to_proposal_v1_2_dict()
        self.assertEqual(proposal["sales_data"]["status"], "partial")
        self.assertEqual(proposal["sales_data"]["observations"], [])
        self.assertEqual(proposal["unit_economics"]["status"], "partial")
        self.assertEqual(proposal["unit_economics"]["calculations"], [])

    def test_unknown_producer_schema_version_is_not_compared_to_local_result_version(self):
        fixture = self._fixture_inputs()
        source = CapturingSource()
        result = MarketResearchAgent(source).run(MarketResearchContext(
            request_id=fixture["request_id"], planning_request=fixture["planning_request"],
            trend_result=fixture["trend_result"], inventory_product_result=fixture["inventory_product_result"],
        ))
        self.assertIsNotNone(source.received)
        self.assertEqual(result.schema_version, "1.0")
        self.assertEqual(result.upstream_context["trend_result"]["schema_version"], "synthetic-trend-producer-version-A")
        self.assertNotEqual(result.upstream_context["trend_result"]["schema_version"], result.schema_version)

    def test_cli_accepts_distinct_producer_schema_versions_without_claiming_common_version(self):
        from market_research_agent.cli import _read_inputs

        fixture = self._fixture_inputs()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json") as stream:
            json.dump(fixture, stream)
            stream.flush()
            planning, trend, inventory = _read_inputs(stream.name)
        self.assertEqual(planning["schema_version"], "1.0")
        self.assertEqual(trend["schema_version"], "synthetic-trend-producer-version-A")
        self.assertEqual(inventory["schema_version"], "synthetic-inventory-producer-version-B")

    def test_missing_malformed_or_mismatched_upstream_request_ids_are_rejected(self):
        fixture = self._fixture_inputs()
        variants = (
            ("missing", {"status": "ok"}, "UPSTREAM_REQUEST_ID_INVALID"),
            ("blank", {"request_id": " ", "status": "ok"}, "UPSTREAM_REQUEST_ID_INVALID"),
            ("non-string", {"request_id": 123, "status": "ok"}, "UPSTREAM_REQUEST_ID_INVALID"),
            ("mismatch", {"request_id": "another-request", "status": "ok"}, "UPSTREAM_REQUEST_ID_MISMATCH"),
            ("bad-status", {"request_id": fixture["request_id"], "status": "complete"}, "UPSTREAM_CONTRACT_INVALID"),
            ("bad-version-type", {"request_id": fixture["request_id"], "schema_version": 3, "status": "ok"}, "UPSTREAM_CONTRACT_INVALID"),
        )
        for label, trend, expected_code in variants:
            with self.subTest(label=label):
                source = CapturingSource()
                result = MarketResearchAgent(source).run(MarketResearchContext(
                    request_id=fixture["request_id"], planning_request=fixture["planning_request"],
                    trend_result=trend,
                ))
                self.assertIsNone(source.received)
                self.assertEqual(result.status, ResultStatus.ERROR)
                self.assertEqual(result.errors[0].code, expected_code)
                self.assertIsNone(result.upstream_context)

    def test_conflicting_outer_and_nested_request_ids_are_rejected(self):
        fixture = self._fixture_inputs()
        trend = {
            "request_id": fixture["request_id"],
            "result": {"request_id": "different-request", "status": "partial"},
        }
        source = CapturingSource()
        result = MarketResearchAgent(source).run(MarketResearchContext(
            request_id=fixture["request_id"], planning_request=fixture["planning_request"], trend_result=trend,
        ))
        self.assertIsNone(source.received)
        self.assertEqual(result.errors[0].code, "UPSTREAM_REQUEST_ID_MISMATCH")

    def test_all_supported_upstream_states_are_retained_without_inventing_source_results(self):
        fixture = self._fixture_inputs()
        for state in ("ok", "partial", "no_results", "error"):
            with self.subTest(state=state):
                source = CapturingSource()
                trend = {
                    "request_id": fixture["request_id"],
                    "schema_version": "synthetic-trend-producer-version-A",
                    "status": state,
                    "producer_payload": {"state_echo": state},
                }
                result = MarketResearchAgent(source).run(MarketResearchContext(
                    request_id=fixture["request_id"], planning_request=fixture["planning_request"],
                    trend_result=trend,
                ))
                self.assertIsNotNone(source.received)
                self.assertEqual(result.upstream_context["trend_result"], trend)
                self.assertEqual(result.status, ResultStatus.NO_RESULTS)
                self.assertIsNone(result.market_data)
                self.assertIsNone(result.sales_data)


if __name__ == "__main__":
    unittest.main()
