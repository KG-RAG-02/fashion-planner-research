from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from market_research_agent import (
    MarketDataResult,
    MarketObservation,
    MarketResearchAgent,
    MarketResearchContext,
    MissingData,
    SalesDataResult,
    UnitEconomicsResult,
)


class SerializationTests(unittest.TestCase):
    def test_sales_data_is_only_emitted_by_unratified_proposal_serializer(self) -> None:
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="req-123", planning_request={})
        )
        result = replace(result, sales_data=SalesDataResult(status="no_results"))

        self.assertNotIn("sales_data", result.to_dict())
        proposed = result.to_proposal_dict()
        self.assertEqual(proposed["schema_version"], "1.1-proposal")
        self.assertEqual(proposed["sales_data"]["status"], "no_results")
        self.assertNotIn("missing_data", proposed["sales_data"])

    def test_default_result_is_json_compatible_and_matches_schema_envelope(self) -> None:
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="req-123", planning_request={})
        )
        payload = result.to_dict()
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        schema_path = (
            Path(__file__).parents[1]
            / "contracts"
            / "schemas"
            / "market-research-result.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(set(decoded), set(schema["required"]))
        self.assertEqual(decoded["status"], "no_results")
        self.assertEqual(decoded["schema_version"], "1.0")
        self.assertEqual(decoded["agent_id"], "sales-market-agent")

    def test_v1_2_proposal_is_explicit_and_separates_data_domains(self) -> None:
        result = MarketResearchAgent().run(
            MarketResearchContext(
                request_id="req-123",
                planning_request={},
                requested_category="outerwear",
                requested_metrics=("market_size", "retail_sales_index", "unit_margin"),
            )
        )
        proposed = result.to_proposal_v1_2_dict()

        self.assertEqual(proposed["schema_version"], "1.2-proposal")
        self.assertEqual(proposed["requested_scope"]["category"], "outerwear")
        self.assertEqual(proposed["requested_scope"]["metrics"], ["market_size", "retail_sales_index", "unit_margin"])
        self.assertEqual(proposed["sales_data"]["status"], "no_results")
        self.assertEqual(proposed["market_data"]["status"], "no_results")
        self.assertEqual(proposed["unit_economics"]["status"], "no_results")
        self.assertTrue(all(item["reason"] == "source_not_configured" for item in proposed["missing_data"]))
        self.assertNotIn("sales_data", result.to_dict())
        self.assertNotIn("market_data", result.to_proposal_dict())

    def test_proposal_observations_require_traceable_permission_and_preserve_zero(self) -> None:
        observation = MarketObservation(
            observation_id="market-1",
            metric_name="online_market_size",
            measure_type="market_estimate",
            value=0,
            unit="KRW",
            evidence_ids=("market:E-001",),
            geography="KR",
            channel="online",
            as_of="2026-11-01",
        )
        from market_research_agent import EvidenceRecord, PermissionStatus, ResultStatus, SourceReport

        class StaticSource:
            def research(self, _context):
                return SourceReport(
                    status=ResultStatus.OK,
                    evidence=(EvidenceRecord(
                        evidence_id="market:E-001", source_id="source-1",
                        source_type="official_statistics", source_name="Official statistics",
                        permission_status=PermissionStatus.AUTHORIZED,
                    ),),
                    market_data=MarketDataResult("ok", observations=(observation,)),
                )

        result = MarketResearchAgent(StaticSource()).run(
            MarketResearchContext(request_id="req-123", planning_request={})
        )
        proposed = result.to_proposal_v1_2_dict()
        self.assertEqual(proposed["market_data"]["observations"][0]["value"], 0)
        self.assertEqual(proposed["market_data"]["observations"][0]["evidence_ids"], ("market:E-001",))

    def test_market_values_without_authorized_evidence_are_not_emitted(self) -> None:
        observation = MarketObservation(
            observation_id="market-1", metric_name="market_size",
            measure_type="market_estimate", value=12, unit="KRW",
            evidence_ids=("market:unknown",),
        )
        from market_research_agent import ResultStatus, SourceReport

        class StaticSource:
            def research(self, _context):
                return SourceReport(
                    status=ResultStatus.OK,
                    market_data=MarketDataResult("ok", observations=(observation,)),
                )

        result = MarketResearchAgent(StaticSource()).run(
            MarketResearchContext(request_id="req-123", planning_request={})
        )
        self.assertEqual(result.market_data.status, "partial")
        self.assertEqual(result.market_data.observations, ())
        self.assertIn("INVALID_MARKET_EVIDENCE", {item.code for item in result.errors})


if __name__ == "__main__":
    unittest.main()
