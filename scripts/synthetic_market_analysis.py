"""Generate and analyze a clearly synthetic market time series for demos.

No external source, H&M file, credential, or production permission is used.
The output is a local proposal example and is not business evidence.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "examples" / "synthetic_analysis"
SYNTHETIC_NOTICE = (
    "합성 데이터이며 실제 시장 관측·예측이 아니다. 사업 의사결정에 사용할 수 없다."
)


def _months() -> list[tuple[int, int]]:
    return [(year, month) for year in (2024, 2025) for month in range(1, 13)]


def generate(seed: int, output_dir: Path) -> dict[str, Any]:
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows: list[tuple[int, int, float]] = []
    seasonal_shape = [76, 79, 88, 98, 108, 112, 105, 96, 91, 103, 118, 132]
    for year, month in _months():
        offset = 0 if year == 2024 else 12
        seasonal = seasonal_shape[month - 1]
        trend = offset * 0.65
        noise = rng.uniform(-3.0, 3.0)
        raw_rows.append((year, month, seasonal + trend + noise))
    base_mean = sum(value for year, _month, value in raw_rows if year == 2024) / 12
    rows: list[dict[str, Any]] = []
    for year, month, raw_value in raw_rows:
        index = round(raw_value / base_mean * 100, 1)
        rows.append({
            "observation_id": f"syn-market-{year}-{month:02d}",
            "metric_name": "retail_sales_index",
            "measure_type": "aggregate_index",
            "value": index,
            "unit": "index_2024_annual_mean_100",
            "period_start": f"{year}-{month:02d}-01",
            "period_end": f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
            "period_grain": "month",
            "geography": "KR",
            "channel": "online",
            "category": "women_light_outerwear",
            "population": "synthetic_retail_panel_v1",
            "metric_definition": "가상 소매 거래액 지수; 2024년 월평균을 100으로 둔 합성 시계열",
            "evidence_id": f"syn-evidence-{year}-{month:02d}",
            "synthetic": "true",
            "data_nature": "synthetic_test",
            "seed": seed,
        })
    csv_path = output_dir / "synthetic_market_observations.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    mean_2024 = sum(float(row["value"]) for row in rows[:12]) / 12
    mean_2025 = sum(float(row["value"]) for row in rows[12:]) / 12
    yoy = round((mean_2025 / mean_2024 - 1) * 100, 2)
    evidence = [{
        "evidence_id": row["evidence_id"],
        "source_id": "synthetic-generator",
        "source_type": "synthetic_test_dataset",
        "source_name": "Deterministic synthetic market series",
        "locator": f"synthetic_market_observations.csv#{row['observation_id']}",
        "published_at": None,
        "observed_at": f"{row['period_end']}T00:00:00Z",
        "fetched_at": None,
        "permission_status": "authorized_for_synthetic_testing_only",
        "manifest_id": f"synthetic-seed-{seed}",
        "synthetic": True,
        "data_nature": "synthetic_test",
    } for row in rows]
    result = {
        "schema_version": "1.0-proposal",
        "request_id": f"synthetic-demo-seed-{seed}",
        "agent_id": "market-research-agent",
        "status": "partial",
        "summary": (
            f"합성 시계열 예시에서 2025년 월평균 지수는 2024년보다 {yoy}% 높다. "
            "실제 시장 결과가 아니며 업무 판단에 사용할 수 없다."
        ),
        "sales_analysis": {
            "status": "no_results",
            "source": "H&M Kaggle historical data",
            "observations": [],
            "calculations": [],
            "limitations": [
                "Kaggle 데이터 사용 권한이 이 작업에서 확인되지 않아 adapter의 권한 gate를 유지했다.",
                "따라서 H&M 파일을 읽거나 판매 수치를 산출하지 않았다.",
                "사용 승인이 확인되고 명시적으로 활성화된 뒤 별도 분석이 필요하다.",
            ],
            "missing_data": [{
                "field": "sales_analysis",
                "reason": "permission_not_confirmed",
                "message": "H&M source usage authorization is unconfirmed; observations withheld.",
            }],
        },
        "market_analysis": {
            "status": "ok",
            "synthetic": True,
            "data_nature": "synthetic_test",
            "permission_status": "authorized_for_synthetic_testing_only",
            "usage_restriction": SYNTHETIC_NOTICE,
            "requested_scope": {
                "geography": "KR", "channel": "online",
                "category": "women_light_outerwear", "period": "2024-01 to 2025-12",
            },
            "observations": rows,
            "calculations": [{
                "calculation_id": "synthetic-yoy-mean-index-2025",
                "metric_name": "year_over_year_change_in_annual_mean_index",
                "value": yoy,
                "unit": "percent",
                "formula_id": "percentage_change_of_annual_mean",
                "formula_version": "1.0",
                "input_observation_ids": [row["observation_id"] for row in rows],
                "calculation_reference": f"({mean_2025:.4f} / {mean_2024:.4f} - 1) * 100",
            }],
            "evidence": evidence,
            "limitations": [
                SYNTHETIC_NOTICE,
                "지수 값은 가상이며 절대 거래액·판매량·시장점유율을 나타내지 않는다.",
                "계절 패턴과 무작위 변동은 generator 규칙으로 만든 것이며 실제 분포를 추정하지 않는다.",
            ],
            "missing_data": [],
        },
        "comparability": [{
            "status": "not_comparable",
            "sales_observation_id": None,
            "market_observation_ids": [row["observation_id"] for row in rows],
            "mismatches": ["sales observations withheld", "different source nature", "population unknown for H&M mapping"],
            "reason": "판매값이 없고 H&M historical purchase rows와 합성 market index는 의미·단위·모집단이 다르다.",
        }],
        "evidence": evidence,
        "limitations": [SYNTHETIC_NOTICE, "H&M 판매 분석은 권한 확인 전까지 미실행이다."],
        "errors": [],
        "generation": {
            "seed": seed,
            "generator": "scripts/synthetic_market_analysis.py",
            "rule": "monthly seasonal template + linear drift + uniform noise in [-3, 3]",
            "seasonal_template_2024": seasonal_shape,
            "base_period_normalization": "divide each synthetic value by the generated 2024 mean and multiply by 100",
            "monthly_drift_per_elapsed_month": 0.65,
            "noise_distribution": "uniform(-3, 3)",
        },
    }
    (output_dir / "analysis_output.proposal.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_svg(rows, output_dir / "analysis_comparison.svg", yoy)
    manifest = {
        "manifest_id": f"synthetic-seed-{seed}", "synthetic": True,
        "data_nature": "synthetic_test",
        "permission_status": "authorized_for_synthetic_testing_only",
        "operational_eligible": False, "seed": seed,
        "rows": len(rows), "usage_restriction": SYNTHETIC_NOTICE,
        "empirical_calibration": False,
        "value_basis": "generator assumptions only; not calibrated to observed market values",
        "metric_definition": "Synthetic retail sales index, with generated 2024 monthly mean normalized to 100",
        "shape_reference": "Statistics Korea Online Shopping Trends publication dimensions only; no observed values copied or used for calibration",
        "shape_reference_url": "https://www.kostat.go.kr/board.es?act=view&bid=241&list_no=437829&mid=a10301010000",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _write_svg(rows: list[dict[str, Any]], path: Path, yoy: float) -> None:
    width, height = 1100, 620
    x0, y0, plot_w, plot_h = 90, 145, 900, 340
    values = [float(row["value"]) for row in rows]
    low, high = 60, 145
    points = []
    for i, value in enumerate(values):
        x = x0 + i * plot_w / (len(values) - 1)
        y = y0 + plot_h - (value - low) / (high - low) * plot_h
        points.append(f"{x:.1f},{y:.1f}")
    path_points = " ".join(points)
    ticks = []
    for value in (60, 80, 100, 120, 140):
        y = y0 + plot_h - (value - low) / (high - low) * plot_h
        ticks.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0+plot_w}" y2="{y:.1f}" stroke="#d7dee8"/><text x="{x0-14}" y="{y+5:.1f}" text-anchor="end">{value}</text>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#fff"/><style>text{{font-family:Arial,sans-serif;fill:#182230}} .small{{font-size:14px}} .title{{font-size:24px;font-weight:bold}} .sub{{font-size:16px}}</style>
<text x="55" y="42" class="title">합성 시장 지수 예시 · 실제 시장 데이터 아님</text>
<text x="55" y="72" class="sub">KR · online · women_light_outerwear · 월별 retail_sales_index · 기준: 2024 연평균=100</text>
{''.join(ticks)}<line x1="{x0}" y1="{y0+plot_h}" x2="{x0+plot_w}" y2="{y0+plot_h}" stroke="#526174"/><polyline points="{path_points}" fill="none" stroke="#2367d1" stroke-width="4"/>
<line x1="{x0}" y1="{y0+plot_h-(100-low)/(high-low)*plot_h:.1f}" x2="{x0+plot_w}" y2="{y0+plot_h-(100-low)/(high-low)*plot_h:.1f}" stroke="#ef7d22" stroke-dasharray="8 6"/><text x="{x0+plot_w-10}" y="{y0+plot_h-(100-low)/(high-low)*plot_h-8:.1f}" text-anchor="end" class="small">기준 지수 100</text>
<text x="{x0}" y="{y0+plot_h+30}" class="small">2024-01</text><text x="{x0+plot_w/2}" y="{y0+plot_h+30}" text-anchor="middle" class="small">2024-12 / 2025-01</text><text x="{x0+plot_w}" y="{y0+plot_h+30}" text-anchor="end" class="small">2025-12</text>
<text x="{x0+plot_w/2}" y="{y0+plot_h+60}" text-anchor="middle" class="small">월별 기간</text><text x="24" y="{y0+plot_h/2}" transform="rotate(-90 24 {y0+plot_h/2})" text-anchor="middle" class="small">지수 (index points)</text>
<rect x="55" y="540" width="990" height="52" rx="8" fill="#fff3e6"/><text x="72" y="562" class="small">2025 월평균 대 2024 월평균 변화율(가상 계산): {yoy}% · 계절성/잡음은 seed로 생성</text><text x="72" y="582" class="small">H&amp;M 판매 패널: 권한 확인 전 수치 미조회 · 비교 결과: not_comparable</text></svg>'''
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261110)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = generate(args.seed, args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "status": result["status"], "market_observations": len(result["market_analysis"]["observations"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
