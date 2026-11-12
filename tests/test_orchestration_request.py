from __future__ import annotations

import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from market_research_agent import MarketResearchAgent, ResultStatus, SourceReport
from market_research_agent.domain.models import FindingKind, ResearchFinding
from market_research_agent.orchestration_request import (
    CONTRACT_VERSION,
    CanonicalRequestError,
    context_from_canonical_request,
)


def canonical_request() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "canonical-request-001",
        "created_at": "2026-11-11T09:00:00+09:00",
        "locale": "ko-KR",
        "timezone": "Asia/Seoul",
        "raw_query": "내년 여름에 유행할 여성용 니트 제안해줘",
        "query_parser": {
            "name": "project08-structured-query",
            "version": "1.0",
            "reference_time": "2026-11-11T09:00:00+09:00",
        },
        "intent": {
            "primary": "product_opportunity_research",
            "requested_action": "recommend",
            "confidence": 0.98,
        },
        "scope": {
            "category": {"value": "Knitwear", "certainty": "inferred", "source": "raw_query"},
            "gender": {"value": "women", "certainty": "explicit", "source": "raw_query"},
            "season": {"value": "summer", "certainty": "inferred", "source": "raw_query"},
            "period": {
                "start": "2027-06-01", "end": "2027-08-31", "certainty": "resolved",
                "rule_version": "fashion-season/1", "source": "relative_time",
            },
            "geography": None,
            "channel": None,
            "metrics": [],
            "ambiguities": [],
        },
        "requested_agents": ["trend", "inventory", "sales_market"],
    }


class CanonicalRequestTests(unittest.TestCase):
    def test_structured_request_maps_scope_without_reparsing_or_sibling_results(self):
        request = canonical_request()
        expected = copy.deepcopy(request)

        context = context_from_canonical_request(request)

        self.assertEqual(context.request_id, request["request_id"])
        self.assertEqual(context.requested_query, request["raw_query"])
        self.assertEqual(context.requested_category, "Knitwear")
        self.assertEqual(context.requested_period, "2027-06-01..2027-08-31")
        self.assertEqual(context.requested_geography, None)
        self.assertEqual(context.requested_channel, None)
        self.assertEqual(context.explicit_scope["gender"], "women")
        self.assertEqual(context.inferred_scope["category"]["certainty"], "inferred")
        self.assertEqual(context.inferred_scope["period"]["rule_version"], "fashion-season/1")
        self.assertEqual(context.parser_version, "project08-structured-query/1.0")
        self.assertEqual(context.query_intent, "unsupported")
        self.assertEqual(context.query_ambiguities, ())
        self.assertIsNone(context.trend_result)
        self.assertIsNone(context.inventory_product_result)
        self.assertEqual(context.canonical_request, expected)

    def test_missing_upstream_agent_results_do_not_make_independent_analysis_partial(self):
        request = canonical_request()

        class ProposalSource:
            def research(self, context):
                return SourceReport(
                    status=ResultStatus.OK,
                    findings=(ResearchFinding(
                        finding_id="proposal-1", topic="scope", statement="Structured scope accepted.",
                        kind=FindingKind.PROPOSAL,
                    ),),
                )

        result = MarketResearchAgent(ProposalSource()).run(context_from_canonical_request(request))

        self.assertEqual(result.status, ResultStatus.OK)
        proposal = result.to_proposal_v1_3_dict()
        self.assertEqual(proposal["requested_scope"]["canonical_request"], request)
        self.assertEqual(proposal["requested_scope"]["gender"], "women")
        self.assertEqual(proposal["requested_scope"]["period"], "2027-06-01..2027-08-31")
        self.assertNotIn("trend_result", proposal["requested_scope"])
        self.assertNotIn("inventory_product_result", proposal["requested_scope"])

    def test_product_opportunity_request_cannot_activate_hm_purchase_rows(self):
        context = context_from_canonical_request(canonical_request())
        self.assertEqual(context.requested_metrics, ())
        self.assertEqual(context.query_intent, "unsupported")

    def test_cli_accepts_canonical_request_as_a_distinct_input_mode(self):
        from market_research_agent.cli import main

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(canonical_request()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["--canonical-request-json", str(path), "--proposal-v1-3"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["request_id"], "canonical-request-001")
        self.assertEqual(payload["requested_scope"]["canonical_request"]["raw_query"], canonical_request()["raw_query"])
        self.assertEqual(payload["status"], "partial")

    def test_rejects_trend_or_inventory_payloads_at_canonical_boundary(self):
        for field in ("trend_result", "inventory_product_result"):
            request = canonical_request()
            request[field] = {"request_id": request["request_id"], "status": "ok"}
            with self.subTest(field=field), self.assertRaises(CanonicalRequestError):
                context_from_canonical_request(request)

    def test_rejects_unresolved_or_reversed_periods_without_inventing_dates(self):
        unresolved = canonical_request()
        unresolved["scope"]["period"] = {
            "start": None, "end": None, "certainty": "unresolved",
            "rule_version": "fashion-season/1", "source": "relative_time",
        }
        context = context_from_canonical_request(unresolved)
        self.assertIsNone(context.requested_period)
        self.assertEqual(context.inferred_scope["period"]["certainty"], "unresolved")
        self.assertIn("unresolved_period", context.query_ambiguities)

        reversed_period = canonical_request()
        reversed_period["scope"]["period"]["start"] = "2027-09-01"
        with self.assertRaises(CanonicalRequestError):
            context_from_canonical_request(reversed_period)

    def test_unresolved_scope_ambiguity_prevents_source_query(self):
        request = canonical_request()
        request["scope"]["ambiguities"] = ["ambiguous_season_window"]

        class MustNotQuerySource:
            called = False

            def research(self, context):
                self.called = True
                return SourceReport(status=ResultStatus.OK)

        source = MustNotQuerySource()
        result = MarketResearchAgent(source).run(context_from_canonical_request(request))

        self.assertFalse(source.called)
        self.assertEqual(result.status, ResultStatus.NO_RESULTS)
        self.assertIn("unresolved ambiguity", " ".join(result.limitations))

    def test_rejects_invalid_contract_request_id_parser_time_and_timezone(self):
        cases = []
        invalid_version = canonical_request()
        invalid_version["contract_version"] = "vogue-handoff/0.1"
        cases.append(invalid_version)
        invalid_id = canonical_request()
        invalid_id["request_id"] = " "
        cases.append(invalid_id)
        missing_offset = canonical_request()
        missing_offset["query_parser"]["reference_time"] = "2026-11-11T09:00:00"
        cases.append(missing_offset)
        unknown_zone = canonical_request()
        unknown_zone["timezone"] = "Mars/Olympus"
        cases.append(unknown_zone)
        for request in cases:
            with self.subTest(request=request), self.assertRaises(CanonicalRequestError):
                context_from_canonical_request(request)

    def test_rejects_missing_sales_market_capability_and_ambiguous_scope_shape(self):
        no_sales_market = canonical_request()
        no_sales_market["requested_agents"] = ["trend", "inventory"]
        with self.assertRaises(CanonicalRequestError):
            context_from_canonical_request(no_sales_market)

        wrong_scope = canonical_request()
        wrong_scope["scope"].pop("ambiguities")
        with self.assertRaises(CanonicalRequestError):
            context_from_canonical_request(wrong_scope)


if __name__ == "__main__":
    unittest.main()
