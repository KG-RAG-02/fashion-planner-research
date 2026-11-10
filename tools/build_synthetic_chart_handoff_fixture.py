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
    artifact = {
        "artifact_id": "synthetic-market-value-trend-v1",
        "artifact_type": "chart",
        "format": "image/svg+xml",
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
            "observation_ids": [row["observation_id"] for row in chart_rows],
            "evidence_ids": [row["evidence_ids"][0] for row in chart_rows],
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
        "contract_version": "vogue-handoff/0.1-proposal",
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
    # Guard the core preservation invariant: the producer body is embedded unchanged.
    assert envelope["result"] == result
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "request_id": envelope["request_id"], "chart_sha256": chart_sha256, "result_body_preserved": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
