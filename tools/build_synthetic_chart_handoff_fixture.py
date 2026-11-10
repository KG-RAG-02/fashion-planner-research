"""Bundle the generated synthetic Project_07 result and chart as a proposal fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "examples/synthetic_market_scenario/generated"
DEFAULT_OUT = ROOT / "examples/fixtures/synthetic_chart_handoff/handoff.proposal.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = json.loads((args.source_dir / "analysis_output.proposal.json").read_text(encoding="utf-8"))
    manifest = json.loads((args.source_dir / "manifest.json").read_text(encoding="utf-8"))
    chart_path = args.source_dir / "market_value_trend.svg"
    chart_bytes = chart_path.read_bytes()
    chart_sha256 = hashlib.sha256(chart_bytes).hexdigest()
    chart_rows = [
        row for row in result["market_analysis"]["observations"]
        if row["metric_name"] == "market_transaction_value" and row["scope_level"] == "parent_category_total"
    ]
    if not chart_rows:
        raise ValueError("Synthetic result has no parent-category market observations for the chart.")

    observation_ids = {row.get("observation_id") for row in result["market_analysis"]["observations"]}
    evidence_ids = {item.get("evidence_id") for item in result.get("evidence", [])}
    chart_observation_ids = [row.get("observation_id") for row in chart_rows]
    chart_evidence_ids = [evidence_id for row in chart_rows for evidence_id in row.get("evidence_ids", [])]
    if any(not item for item in chart_observation_ids) or not set(chart_observation_ids) <= observation_ids:
        raise ValueError("Chart refers to a missing or empty observation_id.")
    if any(not item for item in chart_evidence_ids) or not set(chart_evidence_ids) <= evidence_ids:
        raise ValueError("Chart refers to a missing or empty evidence_id.")
    for field, expected in (
        ("synthetic", True),
        ("data_nature", "synthetic_test"),
        ("permission_status", "authorized_for_synthetic_testing_only"),
    ):
        if result.get(field) != expected:
            raise ValueError(f"Synthetic result has invalid {field} marker.")
    if ("operational_eligible" in result and result["operational_eligible"] is not False):
        raise ValueError("Synthetic result must not be operationally eligible.")
    if manifest.get("operational_eligible") is not False:
        raise ValueError("Synthetic manifest must not be operationally eligible.")

    artifact = {
        "artifact_id": "synthetic-market-value-trend-v1",
        "request_id": result["request_id"],
        "artifact_type": "chart",
        "format": "image/svg+xml",
        "status": "ready",
        "artifact_uri": "fixture://synthetic_market_scenario/generated/market_value_trend.svg",
        "sha256": chart_sha256,
        "byte_length": len(chart_bytes),
        "chart_metadata": {
            "title": "합성 월별 거래액 시나리오 · 실제 시장 결과 아님",
            "chart_type": "monthly_line_series",
            "metric_name": "market_transaction_value",
            "unit": "KRW_million",
            "period_grain": "month",
            "geography": result["requested_scope"]["geography"],
            "channel": result["requested_scope"]["channel"],
            "category": result["requested_scope"]["category"],
            "population": result["requested_scope"]["population"],
            "observation_ids": chart_observation_ids,
            "evidence_ids": chart_evidence_ids,
            "alt_text": "합성 가정으로 생성된 한국 온라인 여성 아우터 월별 거래액 예시. 실제 시장 관측이나 전망이 아니다.",
            "limitations": [
                result["usage_restriction"],
                "계절성·연간 변화·금액 규모는 생성 설정의 가정값이다.",
            ],
        },
        "synthetic": True,
        "data_nature": "synthetic_test",
        "permission_status": "authorized_for_synthetic_testing_only",
        "operational_eligible": False,
    }
    envelope = {
        "contract_version": "vogue-handoff/0.2-proposal",
        "request_id": result["request_id"],
        "producer": {"agent_id": result["agent_id"], "schema_version": result["schema_version"]},
        "status": result["status"],
        "result": result,
        "artifacts": [artifact],
        "synthetic": True,
        "data_nature": "synthetic_test",
        "permission_status": "authorized_for_synthetic_testing_only",
        "operational_eligible": False,
        "fixture_manifest_id": manifest["manifest_id"],
        "limitations": [
            "Handoff envelope and artifact reference are proposals, not a ratified contract or live transport.",
            result["usage_restriction"],
        ],
    }
    # Guard the core preservation and artifact-lineage invariants. Use explicit
    # errors rather than assert so checks also run under optimized Python.
    if envelope["request_id"] != result.get("request_id"):
        raise ValueError("Handoff and producer request_id mismatch.")
    if envelope["result"] != result:
        raise ValueError("Producer result body was changed while building the handoff.")
    if any(item.get("request_id") != envelope["request_id"] for item in envelope["artifacts"]):
        raise ValueError("Artifact request_id does not match the handoff request_id.")
    if hashlib.sha256(chart_bytes).hexdigest() != artifact["sha256"]:
        raise ValueError("Chart artifact hash validation failed.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "request_id": envelope["request_id"], "chart_sha256": chart_sha256, "result_body_preserved": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
