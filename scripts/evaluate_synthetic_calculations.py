#!/usr/bin/env python3
"""Evaluate synthetic-only deterministic calculation gates on a versioned suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_research_agent.synthetic_rag import SYNTHETIC_MARKERS, _format_calculations

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "tests/fixtures/synthetic_rag_analysis/calculation_eval/v1/cases.json"

BASE_ROW: dict[str, Any] = {
    "source_id": "synthetic-calculation-eval-source",
    "metric_name": "scenario_market_value",
    "metric_definition": "Controlled synthetic fixture value for calculation gate testing.",
    "measure_type": "market_estimate",
    "unit": "KRW_million",
    "currency": "KRW",
    "period_grain": "month",
    "geography": "KR",
    "channel": "online",
    "gender": "women",
    "category": "knitwear",
    "season": "SS",
    "season_year": 2028,
    "population": "synthetic-panel-a",
    "product_mapping_id": "synthetic-map",
    "product_mapping_version": "synthetic-1.0",
    "attributes": {},
    "vintage_id": "synthetic-original-v1",
}


def evaluate() -> dict[str, Any]:
    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    if (suite.get("suite_version") != "synthetic-analysis-calculation-eval/1.0"
            or any(suite.get(key) != value for key, value in SYNTHETIC_MARKERS.items())):
        raise ValueError("Calculation evaluation suite is not explicitly test-only synthetic.")

    results = []
    for case in suite.get("cases", []):
        rows = []
        for item in case.get("rows", []):
            row = BASE_ROW | item | SYNTHETIC_MARKERS
            rows.append(row)
        calculations, limitations = _format_calculations(rows)
        expected_count = case["expected_calculation_count"]
        expected_value = case.get("expected_value")
        expected_issue = case.get("expected_issue")
        observed_issue = next((issue for issue in (expected_issue, "period_gap", "zero_baseline", "duplicate_grain", "single_period")
                               if issue and any(issue in message for message in limitations)), None)
        value_match = (expected_value is None and not calculations) or (
            expected_value is not None and len(calculations) == expected_count
            and abs(calculations[0]["value"] - expected_value) < 1e-6
        )
        linked = all(item.get("input_observation_ids") and item.get("evidence_ids")
                     and item.get("synthetic") is True
                     and item.get("data_nature") == "synthetic_test"
                     and item.get("permission_status") == "authorized_for_synthetic_testing_only"
                     and item.get("operational_eligible") is False for item in calculations)
        passed = len(calculations) == expected_count and value_match and observed_issue == expected_issue and linked
        results.append({
            "case_id": case["case_id"], "type": case["type"], "passed": passed,
            "expected_calculation_count": expected_count, "actual_calculation_count": len(calculations),
            "expected_value": expected_value,
            "actual_values": [item["value"] for item in calculations],
            "expected_issue": expected_issue, "actual_issue": observed_issue,
            "limitations": limitations,
        })

    by_type = {}
    for kind in sorted({item["type"] for item in results}):
        subset = [item for item in results if item["type"] == kind]
        by_type[kind] = {"query_count": len(subset), "passed": sum(item["passed"] for item in subset)}
    return {
        "suite_version": suite["suite_version"],
        "synthetic": True,
        "data_nature": "synthetic_test",
        "permission_status": "authorized_for_synthetic_testing_only",
        "operational_eligible": False,
        "case_count": len(results),
        "calculation_gate_accuracy": (sum(item["passed"] for item in results) / len(results)) if results else None,
        "by_type": by_type,
        "failed_case_ids": [item["case_id"] for item in results if not item["passed"]],
        "cases": results,
        "limitations": ["Controlled synthetic calculation-gate inputs only; not market data or business performance.",
                        "This suite evaluates formula gating, not relevance, market validity, or unsupported answer claims."],
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2, allow_nan=False))
