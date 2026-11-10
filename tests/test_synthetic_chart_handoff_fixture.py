"""Validate the synthetic Project_07 chart handoff bundle without network access."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "examples/synthetic_market_scenario/generated"
CHART_PATH = SOURCE_DIR / "market_value_trend.svg"


class SyntheticChartHandoffFixtureTests(unittest.TestCase):
    def test_builder_preserves_result_and_validates_artifact_lineage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "handoff.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/build_synthetic_chart_handoff_fixture.py"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            handoff = json.loads(output.read_text(encoding="utf-8"))

        source_result = json.loads((SOURCE_DIR / "analysis_output.proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(handoff["result"], source_result)
        self.assertEqual(handoff["request_id"], source_result["request_id"])
        self.assertEqual(handoff["producer"]["schema_version"], source_result["schema_version"])
        self.assertEqual(handoff["status"], source_result["status"])
        self.assertTrue(handoff["synthetic"])
        self.assertEqual(handoff["data_nature"], "synthetic_test")
        self.assertEqual(handoff["permission_status"], "authorized_for_synthetic_testing_only")
        self.assertFalse(handoff["operational_eligible"])

        artifact = handoff["artifacts"][0]
        self.assertEqual(artifact["request_id"], handoff["request_id"])
        self.assertEqual(artifact["status"], "ready")
        self.assertEqual(artifact["sha256"], hashlib.sha256(CHART_PATH.read_bytes()).hexdigest())
        self.assertTrue(artifact["synthetic"])
        self.assertEqual(artifact["data_nature"], "synthetic_test")
        self.assertEqual(artifact["permission_status"], "authorized_for_synthetic_testing_only")
        self.assertFalse(artifact["operational_eligible"])

        observation_ids = {
            row["observation_id"]
            for row in source_result["market_analysis"]["observations"]
        }
        evidence_ids = {item["evidence_id"] for item in source_result["evidence"]}
        metadata = artifact["chart_metadata"]
        self.assertTrue(set(metadata["observation_ids"]) <= observation_ids)
        self.assertTrue(set(metadata["evidence_ids"]) <= evidence_ids)

    def test_artifact_failure_review_cases_are_synthetic_and_keep_analysis(self):
        cases_path = ROOT / "examples/fixtures/http_json_handoff/artifact-cases.json"
        fixture = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertTrue(fixture["synthetic"])
        self.assertEqual(fixture["data_nature"], "synthetic_test")
        self.assertEqual(fixture["permission_status"], "authorized_for_synthetic_testing_only")
        self.assertFalse(fixture["operational_eligible"])
        case_ids = {case["case_id"] for case in fixture["cases"]}
        self.assertEqual(
            case_ids,
            {
                "artifact_request_id_mismatch",
                "artifact_hash_mismatch",
                "artifact_evidence_reference_missing",
                "chart_creation_failed_after_analysis",
            },
        )
        failed_case = next(
            case for case in fixture["cases"]
            if case["case_id"] == "chart_creation_failed_after_analysis"
        )
        self.assertTrue(failed_case["expected"]["erase_result"] is False)


if __name__ == "__main__":
    unittest.main()
