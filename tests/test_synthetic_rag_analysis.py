from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from market_research_agent.orchestration_request import CONTRACT_VERSION, CanonicalRequestError
import market_research_agent.synthetic_rag as synthetic_rag
from market_research_agent.synthetic_rag import (
    LIMITATION,
    SyntheticFixtureError,
    analyze_synthetic_request,
    load_fixture_bundle,
)


def request(*, intent="market_observation", metric="scenario_market_value", category="knitwear",
            gender="women", period_start="2028-03-01", period_end="2028-03-31",
            geography="KR", channel="online", ambiguities=None, raw_query="2028년 3월 여성 니트 시장 시나리오"):
    now = "2026-11-11T09:00:00+09:00"
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "synthetic-rag-test-001",
        "created_at": now,
        "locale": "ko-KR",
        "timezone": "Asia/Seoul",
        "raw_query": raw_query,
        "query_parser": {"name": "synthetic-rag-test", "version": "1.0", "reference_time": now},
        "intent": {"primary": intent, "requested_action": "analyze", "confidence": 1.0},
        "scope": {
            "category": {"value": category, "certainty": "explicit", "source": "test"} if category else None,
            "gender": {"value": gender, "certainty": "explicit", "source": "test"} if gender else None,
            "season": {"value": "SS", "certainty": "explicit", "source": "test"},
            "period": {"start": period_start, "end": period_end, "certainty": "resolved", "rule_version": "test/1", "source": "test"},
            "geography": {"value": geography, "certainty": "explicit", "source": "test"} if geography else None,
            "channel": {"value": channel, "certainty": "explicit", "source": "test"} if channel else None,
            "metrics": [metric] if metric else [],
            "ambiguities": ambiguities or [],
        },
        "requested_agents": ["sales_market"],
    }


class SyntheticRagAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_fixture_bundle()

    def test_validates_both_fixture_domains_and_keeps_evidence_sets_separate(self):
        self.assertEqual(len(self.bundle["observations"]["market_like"]), 5184)
        self.assertEqual(len(self.bundle["observations"]["sales_like"]), 864)
        market_ids = set(self.bundle["evidence"]["market_like"])
        sales_ids = set(self.bundle["evidence"]["sales_like"])
        self.assertFalse(market_ids & sales_ids)
        self.assertEqual(len(self.bundle["documents"]), 3)

    def test_market_observation_returns_only_structured_match_with_lineage(self):
        result = analyze_synthetic_request(request(), mode="synthetic_test", bundle=self.bundle)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["market_like"]["unit"], "KRW_million")
        self.assertEqual(result["sales_like"]["status"], "not_queried")
        self.assertTrue(result["market_like"]["observations"])
        self.assertTrue(all(row["evidence_ids"] for row in result["market_like"]["observations"]))
        self.assertTrue(all(row["synthetic"] and row["data_nature"] == "synthetic_test" and not row["operational_eligible"] for row in result["market_like"]["observations"]))
        self.assertTrue(all(item["locator"] for row in result["market_like"]["observations"] for item in row["evidence"]))
        self.assertEqual(result["requested_scope"]["raw_query"], request()["raw_query"])
        self.assertTrue(result["synthetic"])
        self.assertFalse(result["operational_eligible"])
        self.assertIn("합성 테스트", result["limitations"][0])

    def test_sales_and_market_sources_do_not_blend(self):
        sales_request = request(intent="sales_observation", metric="scenario_units_sold")
        result = analyze_synthetic_request(sales_request, mode="synthetic_test", bundle=self.bundle)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sales_like"]["unit"], "item_units")
        self.assertEqual(result["market_like"]["status"], "not_queried")
        self.assertTrue(all(row["source_id"] == "synthetic-generator-query-coverage-sales" for row in result["sales_like"]["observations"]))

    def test_unsupported_revenue_market_size_and_intent_mismatch_are_not_reinterpreted(self):
        for metric in ("revenue", "market_size", "demand", "purchase_rows"):
            result = analyze_synthetic_request(request(metric=metric), mode="synthetic_test", bundle=self.bundle)
            self.assertEqual(result["status"], "no_results")
            self.assertEqual(result["reason"], "unsupported_metric")
            self.assertFalse(result["market_like"]["observations"])
            self.assertFalse(result["sales_like"]["observations"])
        mismatch = analyze_synthetic_request(request(intent="sales_observation", metric="scenario_market_value"), mode="synthetic_test", bundle=self.bundle)
        self.assertEqual(mismatch["reason"], "metric_intent_mismatch")

    def test_ambiguity_stops_before_loading_sources_and_no_scope_defaults_are_added(self):
        ambiguous = request(ambiguities=["ambiguous_metric"])
        result = analyze_synthetic_request(ambiguous, mode="synthetic_test")
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["reason"], "ambiguous_request")
        unscoped = request(category=None, gender=None, geography=None, channel=None)
        result = analyze_synthetic_request(unscoped, mode="synthetic_test", bundle=self.bundle)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["requested_scope"]["explicit"], {"season": "SS"})
        self.assertIn("scope_not_specified:category", result["retrieval"]["scope_limitations"])
        self.assertIn("scope_not_specified:gender", result["retrieval"]["scope_limitations"])

    def test_inferred_category_is_visible_but_not_promoted_to_a_hard_filter(self):
        query = request()
        query["scope"]["category"]["certainty"] = "inferred"
        result = analyze_synthetic_request(query, mode="synthetic_test", bundle=self.bundle)
        categories = {row["category"] for row in result["market_like"]["observations"]}
        self.assertGreater(len(categories), 1)
        self.assertEqual(result["retrieval"]["inferred_scope_not_used_as_hard_filter"]["category"], "knitwear")

    def test_hard_scope_mismatch_and_no_data_remain_no_results(self):
        result = analyze_synthetic_request(request(category="nonexistent"), mode="synthetic_test", bundle=self.bundle)
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["reason"], "no_matching_observations")
        self.assertGreater(result["retrieval"]["excluded_counts"]["scope_mismatch:category"], 0)

    def test_definition_lookup_returns_only_explanatory_document_evidence(self):
        query = request(intent="definition_lookup", metric=None, category=None,
                        raw_query="합성 조사 방법론 생성 설정 설명")
        query["scope"]["period"] = {"start": "2024-01-01", "end": "2025-12-31", "certainty": "resolved", "rule_version": "test/1", "source": "test"}
        query["scope"]["season"] = None
        result = analyze_synthetic_request(query, mode="synthetic_test", bundle=self.bundle)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["retrieval"]["domain"], "explanatory_documents")
        self.assertTrue(result["retrieval"]["document_hits"])
        for hit in result["retrieval"]["document_hits"]:
            self.assertEqual(self.bundle["document_evidence"][hit["evidence_id"]]["content_hash"], hit["content_hash"])
        self.assertFalse(result["market_like"]["observations"])

    def test_synthetic_mode_is_mandatory_and_invalid_canonical_request_is_rejected(self):
        with self.assertRaises(SyntheticFixtureError):
            analyze_synthetic_request(request(), mode="production")
        invalid = request()
        invalid["trend_result"] = {"status": "ok"}
        with self.assertRaises(CanonicalRequestError):
            analyze_synthetic_request(invalid, mode="synthetic_test", bundle=self.bundle)

    def test_fixture_roots_cannot_be_overridden_by_arbitrary_paths(self):
        with self.assertRaises(SyntheticFixtureError):
            load_fixture_bundle(Path("/tmp/not-an-allowlisted-fixture"), ROOT / "examples/synthetic_market_scenario/generated")

    def test_fixture_validation_rejects_operational_promotion_missing_lineage_and_duplicate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory)
            coverage = copied / "coverage"
            scenario = copied / "scenario"
            shutil.copytree(ROOT / "examples/query_coverage/generated", coverage)
            shutil.copytree(ROOT / "examples/synthetic_market_scenario/generated", scenario)
            original_allowlist = synthetic_rag.ALLOWED_FIXTURE_ROOTS
            synthetic_rag.ALLOWED_FIXTURE_ROOTS = (coverage, scenario)
            try:
                docs_path = scenario / "documents.jsonl"
                docs = [json.loads(line) for line in docs_path.read_text(encoding="utf-8").splitlines()]
                docs[0]["operational_eligible"] = True
                docs_path.write_text("".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs), encoding="utf-8")
                with self.assertRaises(SyntheticFixtureError):
                    load_fixture_bundle(coverage, scenario)
                docs[0]["operational_eligible"] = False
                docs[0]["evidence_id"] = "missing-evidence-id"
                docs_path.write_text("".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs), encoding="utf-8")
                with self.assertRaises(SyntheticFixtureError):
                    load_fixture_bundle(coverage, scenario)
                docs[0]["evidence_id"] = "syn-doc-ev-method-001"
                docs_path.write_text("".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs), encoding="utf-8")
                evidence_path = scenario / "evidence.json"
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                method_evidence_index = next(index for index, item in enumerate(evidence) if item["evidence_id"] == "syn-doc-ev-method-001")
                correct_hash = evidence[method_evidence_index]["content_hash"]
                evidence[method_evidence_index]["content_hash"] = "0" * 64
                evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(SyntheticFixtureError):
                    load_fixture_bundle(coverage, scenario)
                evidence[method_evidence_index]["content_hash"] = correct_hash
                evidence[1]["evidence_id"] = evidence[0]["evidence_id"]
                evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(SyntheticFixtureError):
                    load_fixture_bundle(coverage, scenario)
            finally:
                synthetic_rag.ALLOWED_FIXTURE_ROOTS = original_allowlist

    def test_real_zero_value_is_not_treated_as_no_data(self):
        altered = copy.deepcopy(self.bundle)
        altered["observations"]["market_like"][0]["value"] = 0
        row = altered["observations"]["market_like"][0]
        query = request(category=row["category"], gender=row["gender"], geography=row["geography"], channel=row["channel"],
                        raw_query="synthetic observation")
        query["scope"]["period"] = {"start": row["period_start"], "end": row["period_end"], "certainty": "resolved", "rule_version": "test/1", "source": "test"}
        query["scope"]["season"]["value"] = row["season"]
        result = analyze_synthetic_request(query, mode="synthetic_test", bundle=altered)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any(item["value"] == 0 for item in result["market_like"]["observations"]))

    def test_cli_requires_explicit_synthetic_test_mode_and_returns_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(json.dumps(request()), encoding="utf-8")
            script = ROOT / "scripts/run_synthetic_rag_analysis.py"
            denied = subprocess.run([sys.executable, str(script), "--request", str(request_path)], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(denied.returncode, 0)
            run = subprocess.run([sys.executable, str(script), "--mode", "synthetic_test", "--request", str(request_path)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertTrue(payload["synthetic"])
        self.assertEqual(payload["data_nature"], "synthetic_test")
        self.assertFalse(payload["operational_eligible"])

    def test_summary_and_numeric_findings_are_evidence_bounded(self):
        result = analyze_synthetic_request(request(), mode="synthetic_test", bundle=self.bundle)
        returned = set(result["retrieval"]["returned_evidence_ids"])
        for finding in result["analysis"]["findings"]:
            self.assertTrue(finding["evidence_ids"])
            self.assertTrue(set(finding["evidence_ids"]).issubset(returned))


if __name__ == "__main__":
    unittest.main()
