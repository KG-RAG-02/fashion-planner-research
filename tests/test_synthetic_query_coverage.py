from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_query_coverage_fixture import DEFAULT_CONFIG, DEFAULT_OUTPUT, generate, match_request


class SyntheticQueryCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((DEFAULT_OUTPUT / "manifest.json").read_text(encoding="utf-8"))
        cls.market = cls._read_shards("market_observations")
        cls.sales = cls._read_shards("synthetic_sales_observations")
        cls.evidence = cls._read_shards("evidence")
        cls.scenarios = json.loads((DEFAULT_OUTPUT / "query_scenarios.json").read_text(encoding="utf-8"))["scenarios"]

    @classmethod
    def _read_shards(cls, directory: str) -> list[dict]:
        rows = []
        for path in sorted((DEFAULT_OUTPUT / directory).glob("*.jsonl")):
            rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
        return rows

    def test_declared_coverage_and_test_only_markers(self) -> None:
        self.assertEqual(self.manifest["record_counts"], {
            "market_observations": 5184,
            "synthetic_sales_observations": 864,
            "evidence_records": 1728,
        })
        for item in (self.manifest, *self.market[:3], *self.evidence[:3]):
            self.assertIs(item["synthetic"], True)
            self.assertEqual(item["data_nature"], "synthetic_test")
            self.assertEqual(item["permission_status"], "authorized_for_synthetic_testing_only")
            self.assertIs(item["operational_eligible"], False)
        self.assertEqual(self.manifest["query_scenario_count"], 6)
        self.assertEqual(self.manifest["quality_case_count"], 9)
        self.assertEqual(self.manifest["generator_version"], "1.1")

    def test_2028_winter_mens_knitwear_matches_declared_period(self) -> None:
        result = match_request(self.scenarios[0], self.market, self.manifest)
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["period_start"][:7] for item in result["observations"]}, {"2028-12", "2029-01", "2029-02"})
        self.assertEqual({item["gender"] for item in result["observations"]}, {"men"})
        self.assertEqual({item["category"] for item in result["observations"]}, {"knitwear"})

    def test_2028_winter_womens_knitwear_is_explicitly_scoped(self) -> None:
        scenario = next(item for item in self.scenarios if item["scenario_id"] == "2028-winter-women-knitwear")
        result = match_request(scenario, self.market, self.manifest)
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["period_start"][:7] for item in result["observations"]}, {"2028-12", "2029-01", "2029-02"})
        self.assertEqual({item["gender"] for item in result["observations"]}, {"women"})
        self.assertEqual({item["category"] for item in result["observations"]}, {"knitwear"})
        self.assertEqual({item["geography"] for item in result["observations"]}, {"KR"})
        self.assertEqual({item["channel"] for item in result["observations"]}, {"online"})

    def test_omitted_geography_and_channel_are_not_filled_with_defaults(self) -> None:
        scenario = next(item for item in self.scenarios if item["scenario_id"] == "2028-winter-women-knitwear-no-geo-channel")
        result = match_request(scenario, self.market, self.manifest)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["applied_scope"].get("geography"))
        self.assertIsNone(result["applied_scope"].get("channel"))
        self.assertEqual({item["geography"] for item in result["observations"]}, set(self.manifest["coverage"]["geographies"]))
        self.assertEqual({item["channel"] for item in result["observations"]}, set(self.manifest["coverage"]["channels"]))
        self.assertEqual(len({item["period_start"][:7] for item in result["observations"]}), 3)
        example = next(item for item in json.loads((DEFAULT_OUTPUT / "sample_analysis_outputs.json").read_text(encoding="utf-8"))["market_analysis_examples"] if item["scenario_id"] == scenario["scenario_id"])
        self.assertIsNone(example["chart"])
        self.assertTrue(any("must not be merged" in limitation for limitation in example["limitations"]))

    def test_2027_spring_womens_footwear_excludes_black(self) -> None:
        scenario = next(item for item in self.scenarios if item["scenario_id"] == "2027-spring-women-footwear-excluding-black")
        result = match_request(scenario, self.market, self.manifest)
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["period_start"][:7] for item in result["observations"]}, {"2027-03", "2027-04", "2027-05"})
        self.assertNotIn("black", {item["attributes"]["color"] for item in result["observations"]})
        self.assertTrue(all(item["gender"] == "women" and item["category"] == "footwear" for item in result["observations"]))

    def test_unsupported_category_and_out_of_coverage_period_are_not_inferred(self) -> None:
        for scenario in self.scenarios:
            if scenario["scenario_id"] not in {"unsupported-bag-category", "outside-coverage-period"}:
                continue
            result = match_request(scenario, self.market, self.manifest)
            self.assertEqual(result["status"], "no_results")
            self.assertEqual(result["reason"], scenario["expected"]["reason"])
            self.assertEqual(result["observations"], [])

    def test_observation_evidence_lineage_resolves(self) -> None:
        evidence_ids = {row["evidence_id"] for row in self.evidence}
        self.assertTrue(self.market)
        self.assertTrue(all(set(row["evidence_ids"]).issubset(evidence_ids) for row in self.market))
        self.assertTrue(all(set(row["evidence_ids"]).issubset(evidence_ids) for row in self.sales))
        market_evidence_ids = {evidence_id for row in self.market for evidence_id in row["evidence_ids"]}
        sales_evidence_ids = {evidence_id for row in self.sales for evidence_id in row["evidence_ids"]}
        self.assertTrue(market_evidence_ids.isdisjoint(sales_evidence_ids))

    def test_sales_and_market_remain_separate_measures(self) -> None:
        self.assertEqual({row["unit"] for row in self.market}, {"KRW_million"})
        self.assertEqual({row["unit"] for row in self.sales}, {"item_units"})
        self.assertEqual({row["source_id"] for row in self.market}.isdisjoint({row["source_id"] for row in self.sales}), True)

    def test_status_examples_keep_no_results_and_error_without_values(self) -> None:
        examples = json.loads((DEFAULT_OUTPUT / "domain_status_examples.json").read_text(encoding="utf-8"))["examples"]
        self.assertEqual({item["status"] for item in examples}, {"ok", "partial", "no_results", "error"})
        for item in examples:
            if item["status"] in {"no_results", "error"}:
                self.assertEqual(item["observations"], [])
        partial = next(item for item in examples if item["status"] == "partial")
        self.assertTrue(partial["observations"])
        self.assertTrue(partial["missing_data"])

    def test_quality_mutations_are_isolated_and_explicit(self) -> None:
        cases = json.loads((DEFAULT_OUTPUT / "quality_case_fixtures.json").read_text(encoding="utf-8"))["cases"]
        by_id = {case["case_id"]: case for case in cases}
        self.assertEqual(set(by_id), {
            "missing-month", "duplicate-observation", "mapping-conflict", "revision-vintage",
            "out-of-scope-row", "permission-unknown", "missing-evidence", "prompt-injection-document", "source-error",
        })
        self.assertEqual(by_id["duplicate-observation"]["records"][0]["observation_id"], by_id["duplicate-observation"]["records"][1]["observation_id"])
        self.assertEqual(by_id["revision-vintage"]["records"][1]["revision_of"], by_id["revision-vintage"]["records"][0]["observation_id"])
        self.assertEqual(by_id["permission-unknown"]["records"], [])
        self.assertEqual(by_id["missing-evidence"]["records"][0]["evidence_ids"], [])
        self.assertEqual(by_id["prompt-injection-document"]["expected_status"], "ok")

    def test_charts_are_watermarked_and_metadata_is_traceable_but_not_http_artifacts(self) -> None:
        output = json.loads((DEFAULT_OUTPUT / "sample_analysis_outputs.json").read_text(encoding="utf-8"))
        examples = output["market_analysis_examples"]
        for item in examples[:2]:
            chart = item["chart"]
            svg = (DEFAULT_OUTPUT / "charts" / chart["artifact_path"]).read_text(encoding="utf-8")
            self.assertIn("SYNTHETIC TEST ONLY", svg)
            self.assertTrue(chart["observation_ids"])
            self.assertTrue(chart["evidence_ids"])
            self.assertIn("not_supported", chart["http_v01_transfer"])
            self.assertEqual(chart["permission_status"], "authorized_for_synthetic_testing_only")

    def test_generator_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "fixture"
            generate(DEFAULT_CONFIG, out)
            for name in ("manifest.json", "query_scenarios.json", "quality_cases.json", "quality_case_fixtures.json", "sample_analysis_outputs.json", "domain_status_examples.json", "synthetic_methodology_doc.json"):
                self.assertEqual((out / name).read_bytes(), (DEFAULT_OUTPUT / name).read_bytes())
            for name in ("2028-winter-men-knitwear.svg", "2027-spring-women-footwear-excluding-black.svg"):
                self.assertEqual((out / "charts" / name).read_bytes(), (DEFAULT_OUTPUT / "charts" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
