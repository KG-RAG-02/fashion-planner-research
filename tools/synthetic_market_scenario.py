"""Create a linked, explicitly synthetic market-research scenario.

No real market values, external services, credentials, customer data, or H&M
files are read. Values are generator assumptions for technical demonstration.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "examples" / "synthetic_market_scenario" / "scenario-config.json"
DEFAULT_OUTPUT = ROOT / "examples" / "synthetic_market_scenario" / "generated"
NOTICE = "합성 데이터. 실제 시장 관측·예측이 아니며 사업 의사결정에 사용할 수 없음."
PERMISSION = "authorized_for_synthetic_testing_only"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _periods(start: str, months: int) -> list[tuple[int, int]]:
    first = date.fromisoformat(f"{start}-01")
    return [((first.year + (first.month - 1 + i) // 12), (first.month - 1 + i) % 12 + 1) for i in range(months)]


def build(config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    seed = int(config["seed"])
    rng = random.Random(seed)
    scope = config["scope"]
    cats = config["categories"]
    media = config["sales_media"]
    generation = config["generation"]
    periods = _periods(config["period_start"], int(config["period_months"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    obs: list[dict[str, Any]] = []
    monthly_detail: dict[tuple[int, int, str], float] = {}
    monthly_total: dict[tuple[int, int], float] = {}

    def add_observation(*, year: int, month: int, metric: str, value: float,
                        unit: str, category: str, source_category: str,
                        sales_medium: str, scope_level: str, definition: str) -> None:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        oid = f"syn-{metric}-{category}-{sales_medium}-{year}-{month:02d}".replace("_", "-")
        evidence_id = f"syn-ev-{oid}"
        obs.append({
            "observation_id": oid,
            "request_id": config["request_id"],
            "metric_name": metric,
            "measure_type": "aggregate_index" if metric == "retail_sales_index" else "market_estimate",
            "value": round(value, 3 if unit == "KRW_million" else 2),
            "unit": unit,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "period_grain": "month",
            "as_of": end.isoformat() + "T00:00:00Z",
            "geography": scope["geography"],
            "channel": scope["channel"],
            "sales_medium": sales_medium,
            "category": category,
            "source_category": source_category,
            "category_mapping_id": config["mapping"]["mapping_id"],
            "category_mapping_version": config["mapping"]["version"],
            "population": scope["population"],
            "scope_level": scope_level,
            "metric_definition": definition,
            "evidence_ids": [evidence_id],
            "synthetic": True,
            "data_nature": "synthetic_test",
            "permission_status": PERMISSION,
            "vintage": "synthetic-v1",
            "seed": seed,
        })

    seasonal = generation["monthly_seasonality"]
    for year, month in periods:
        elapsed = (year - periods[0][0]) * 12 + month - periods[0][1]
        drift = 1 + float(generation["annual_drift"])
        trend = drift ** (elapsed / 12)
        month_factor = float(seasonal[month - 1])
        total = 0.0
        for cat in cats:
            cat_noise = rng.uniform(-generation["category_noise"], generation["category_noise"])
            monthly_cat = float(cat["base_monthly_value_krw_million"]) * month_factor * trend * (1 + cat_noise)
            media_noise = rng.uniform(-generation["media_noise"], generation["media_noise"])
            shares = {key: float(value) for key, value in media.items()}
            mobile_key = next(iter(shares))
            shares[mobile_key] += media_noise
            other_key = next(key for key in shares if key != mobile_key)
            shares[other_key] -= media_noise
            cat_total = 0.0
            for medium, share in shares.items():
                amount = monthly_cat * share
                cat_total += amount
                monthly_detail[(year, month, cat["category"] + "|" + medium)] = amount
                add_observation(
                    year=year, month=month, metric="market_transaction_value",
                    value=amount, unit="KRW_million", category=cat["category"],
                    source_category=cat["source_category"], sales_medium=medium,
                    scope_level="category_by_sales_medium",
                    definition="가상 패널을 가정한 월별 온라인 거래액; 생성 가정값이며 실제 추정치가 아님.",
                )
            total += cat_total
            add_observation(
                year=year, month=month, metric="market_transaction_value",
                value=cat_total, unit="KRW_million", category=cat["category"],
                source_category=cat["source_category"], sales_medium="all_online",
                scope_level="category_total",
                definition="하위 sales_medium 합계인 가상 월별 카테고리 거래액.",
            )
        add_observation(
            year=year, month=month, metric="market_transaction_value",
            value=total, unit="KRW_million", category=scope["category"],
            source_category="Synthetic parent category", sales_medium="all_online",
            scope_level="parent_category_total",
            definition="합성 하위 카테고리 거래액 합계; 가상 총액이며 실제 시장 규모가 아님.",
        )
        monthly_total[(year, month)] = total

    base_period = [value for (year, _month), value in monthly_total.items() if year == periods[0][0]]
    base_mean = sum(base_period) / len(base_period)
    for (year, month), total in monthly_total.items():
        add_observation(
            year=year, month=month, metric="retail_sales_index",
            value=total / base_mean * 100, unit="index_base_period_mean_100",
            category=scope["category"], source_category="Synthetic parent category",
            sales_medium="all_online", scope_level="parent_category_index",
            definition=f"합성 지수. {periods[0][0]}년 월평균 거래액을 100으로 정규화.",
        )

    observations_path = output_dir / "market_observations.csv"
    fields = list(obs[0].keys())
    with observations_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in obs:
            writer.writerow({**row, "evidence_ids": "|".join(row["evidence_ids"])})

    docs = [
        {
            "document_id": "SYN-DOC-METHOD-001", "document_version": "1",
            "request_id": config["request_id"],
            "evidence_id": "syn-doc-ev-method-001", "source_id": "synthetic-generator",
            "source_name": "합성 조사 방법 설명", "source_type": "synthetic_methodology",
            "locator": "synthetic://methodology/section-1", "page_or_section": "section-1",
            "published_at": config["generated_at_utc"], "content_hash": "",
            "geography": scope["geography"], "channel": scope["channel"],
            "category": scope["category"], "period_start": config["period_start"],
            "period_months": config["period_months"], "population": scope["population"],
            "permission_status": PERMISSION, "synthetic": True, "data_nature": "synthetic_test",
            "text": "가상 온라인 패널의 월별 거래액을 생성하는 예시다. 패널 크기와 금액은 생성 설정이며 실제 조사 표본이나 시장값이 아니다. 계절 계수와 연간 drift, 작은 seeded noise를 적용한다. 실제 사업체 조사·브랜드 매출·SKU 거래를 포함하지 않는다."
        },
        {
            "document_id": "SYN-DOC-TAXONOMY-001", "document_version": config["mapping"]["version"],
            "request_id": config["request_id"],
            "evidence_id": "syn-doc-ev-taxonomy-001", "source_id": "synthetic-generator",
            "source_name": "합성 카테고리 매핑 설명", "source_type": "synthetic_taxonomy",
            "locator": "synthetic://taxonomy/section-1", "page_or_section": "section-1",
            "published_at": config["generated_at_utc"], "content_hash": "",
            "geography": scope["geography"], "channel": scope["channel"],
            "category": scope["category"], "mapping_id": config["mapping"]["mapping_id"],
            "mapping_version": config["mapping"]["version"], "permission_status": PERMISSION,
            "synthetic": True, "data_nature": "synthetic_test",
            "text": "합성 taxonomy는 women_jackets, women_coats, women_light_outerwear를 synthetic_women_outerwear 아래에 둔다. 이 매핑은 설명 예시이며 실제 회사나 공식 통계 분류의 승인 매핑이 아니다."
        },
        {
            "document_id": "SYN-DOC-REVISION-001", "document_version": "1",
            "request_id": config["request_id"],
            "evidence_id": "syn-doc-ev-revision-001", "source_id": "synthetic-generator",
            "source_name": "합성 개정 이력 설명", "source_type": "synthetic_revision_note",
            "locator": "synthetic://revision/section-1", "page_or_section": "section-1",
            "published_at": config["generated_at_utc"], "content_hash": "",
            "geography": scope["geography"], "channel": scope["channel"],
            "category": scope["category"], "permission_status": PERMISSION,
            "synthetic": True, "data_nature": "synthetic_test",
            "text": "개정 QA 사례는 v1 원본을 유지하고 별도 v2 vintage로 추가한다. 예시 개정은 회귀 검증을 위한 임의 수정이며 공식 통계의 개정 폭이나 절차를 재현하지 않는다."
        },
    ]
    for doc in docs:
        doc["content_hash"] = _hash(doc["text"])
    (output_dir / "documents.jsonl").write_text("".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs), encoding="utf-8")

    evidence = [{
        "request_id": config["request_id"],
        "evidence_id": row["evidence_ids"][0], "source_id": "synthetic-generator",
        "source_type": "synthetic_market_observation", "source_name": "합성 시장 관측 fixture",
        "locator": f"synthetic://observation/{row['observation_id']}",
        "published_at": None, "observed_at": row["as_of"],
        "fetched_at": config["generated_at_utc"], "permission_status": PERMISSION,
        "manifest_id": f"synthetic-seed-{seed}", "synthetic": True,
        "data_nature": "synthetic_test", "content_hash": _hash(json.dumps(row, ensure_ascii=False, sort_keys=True)),
    } for row in obs]
    evidence.extend({
        "request_id": config["request_id"],
        "evidence_id": doc["evidence_id"], "source_id": doc["source_id"],
        "source_type": doc["source_type"], "source_name": doc["source_name"],
        "locator": doc["locator"], "published_at": doc["published_at"],
        "observed_at": None, "fetched_at": config["generated_at_utc"],
        "permission_status": PERMISSION, "manifest_id": f"synthetic-seed-{seed}",
        "synthetic": True, "data_nature": "synthetic_test", "content_hash": doc["content_hash"],
    } for doc in docs)

    # Aggregate parent-category monthly values and calculate an annual-mean change.
    by_year = {year: [total for (y, _m), total in monthly_total.items() if y == year] for year, _m in periods}
    years = sorted(by_year)
    first_mean, latest_mean = sum(by_year[years[0]]) / len(by_year[years[0]]), sum(by_year[years[-1]]) / len(by_year[years[-1]])
    yoy = round((latest_mean / first_mean - 1) * 100, 2) if years[-1] == years[0] + 1 else None
    first_year_obs = [row for row in obs if row["metric_name"] == "market_transaction_value" and row["scope_level"] == "parent_category_total" and row["period_start"][:4] == str(years[0])]
    latest_year_obs = [row for row in obs if row["metric_name"] == "market_transaction_value" and row["scope_level"] == "parent_category_total" and row["period_start"][:4] == str(years[-1])]
    first_year_mean = sum(row["value"] for row in first_year_obs) / len(first_year_obs)
    latest_year_mean = sum(row["value"] for row in latest_year_obs) / len(latest_year_obs)
    yoy = round((latest_year_mean / first_year_mean - 1) * 100, 2) if len(years) > 1 and years[-1] == years[0] + 1 and len(first_year_obs) == 12 and len(latest_year_obs) == 12 else None
    result = {
        "schema_version": "market-analysis/0.1-proposal",
        "request_id": config["request_id"], "agent_id": "market-research-agent",
        "status": "partial", "synthetic": True, "data_nature": "synthetic_test",
        "permission_status": PERMISSION, "usage_restriction": NOTICE,
        "summary": "실제 시장 조사 절차를 모사한 합성 시나리오다. 계산은 생성값을 사용했으며 실제 시장 인사이트가 아니다.",
        "requested_scope": {**scope, "period_start": config["period_start"], "period_months": config["period_months"], "metrics": ["market_transaction_value", "retail_sales_index"]},
        "sales_analysis": {"status": "no_results", "source": "H&M Kaggle historical data", "observations": [], "limitations": ["사용 권한이 확인되지 않아 H&M 파일을 읽거나 분석하지 않았다."], "missing_data": [{"field": "sales_analysis", "reason": "permission_not_confirmed"}]},
        "market_analysis": {
            "status": "ok", "synthetic": True, "data_nature": "synthetic_test", "permission_status": PERMISSION,
            "observations": obs,
            "calculations": ([{
                "calculation_id": "syn-annual-market-value-change-v1", "metric_name": "annual_mean_market_transaction_value_change",
                "value": yoy, "unit": "percent", "formula_id": "percentage_change", "formula_version": "1.0-synthetic",
                "input_observation_ids": [row["observation_id"] for row in first_year_obs + latest_year_obs],
                "evidence_ids": [row["evidence_ids"][0] for row in first_year_obs + latest_year_obs],
                "calculation_reference": f"({latest_year_mean:.3f} / {first_year_mean:.3f} - 1) * 100",
                "limitation": "두 생성 연도의 연평균 비교 예시; 실제 성장률이 아님."
            }] if yoy is not None else []),
            "evidence": evidence, "limitations": [NOTICE, "계절성·drift·금액 범위는 생성 가정이며 실제 분포에 보정되지 않았다.", "상위 카테고리와 세부 카테고리는 중복 합산하지 않는다."],
            "missing_data": []
        },
        "findings": ([{
            "finding_id": "syn-finding-market-change-v1", "topic": "합성 거래액 변화",
            "statement": f"합성 생성 시나리오의 연평균 거래액 변화율 계산값은 {yoy}%다. 이는 실제 시장 변화가 아니다.",
            "kind": "interpretation", "evidence_ids": [row["evidence_ids"][0] for row in first_year_obs + latest_year_obs],
            "related_concept_ids": [scope["category"]]
        }] if yoy is not None else []),
        "comparability": [{"status": "not_comparable", "mismatches": ["sales observations withheld", "synthetic market scenario", "measure and population not aligned"], "reason": "H&M 사용 권한 미확인으로 판매값이 없고 합성 시장 추정값은 실제 판매와 비교할 수 없다."}],
        "evidence": evidence, "limitations": [NOTICE, "합성 결과는 실제 시장성 검증이나 판매 전망이 아니다."], "errors": [],
        "generation_metadata": {
            "seed": seed, "generator": "tools/synthetic_market_scenario.py",
            "seasonality_multipliers": seasonal, "annual_drift_assumption": generation["annual_drift"],
            "category_noise_range": [-generation["category_noise"], generation["category_noise"]],
            "sales_media_noise_range": [-generation["media_noise"], generation["media_noise"]],
            "value_calibration": "none; all scale and seasonal parameters are explicit scenario assumptions",
            "period_count": len(periods), "detail_observation_count": len(obs),
        },
    }
    (output_dir / "analysis_output.proposal.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qa = _quality_scenarios(config, obs)
    (output_dir / "quality_scenarios.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_chart(output_dir / "market_value_trend.svg", periods, monthly_total, scope, config)
    _write_request(output_dir / "request.json", config)
    manifest = {
        "manifest_id": f"synthetic-seed-{seed}", "request_id": config["request_id"],
        "synthetic": True, "data_nature": "synthetic_test", "permission_status": PERMISSION,
        "operational_eligible": False, "empirical_calibration": False,
        "generator": "tools/synthetic_market_scenario.py", "seed": seed,
        "observation_count": len(obs), "document_count": len(docs),
        "quality_scenario_count": len(qa["scenarios"]), "generated_at_utc": config["generated_at_utc"],
        "usage_restriction": NOTICE,
        "value_basis": "generator assumptions only; no real market values copied or calibrated",
        "mapping_id": config["mapping"]["mapping_id"], "mapping_version": config["mapping"]["version"],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"observations": len(obs), "documents": len(docs), "quality_scenarios": len(qa["scenarios"]), "yoy_example": yoy}


def _quality_scenarios(config: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    base = next(item for item in observations if item["scope_level"] == "category_by_sales_medium")
    revision = dict(base, observation_id=base["observation_id"] + "-v2", vintage="synthetic-v2", value=round(base["value"] * 1.07, 3), revision_of=base["observation_id"], evidence_ids=["syn-ev-revision-v2"], revision_reason="synthetic QA perturbation; not a real revision")
    unknown = dict(base, observation_id=base["observation_id"] + "-permission-unknown", evidence_ids=["syn-ev-permission-unknown"], permission_status="unknown")
    outlier = dict(base, observation_id=base["observation_id"] + "-outlier", evidence_ids=["syn-ev-outlier"], value=round(base["value"] * 4, 3), qa_tag="synthetic_outlier")
    duplicate = dict(base, qa_tag="duplicate_observation_id")
    unmapped = dict(base, observation_id=base["observation_id"] + "-unmapped", category="unmapped_demo_category", category_mapping_id=None, category_mapping_version=None, evidence_ids=["syn-ev-unmapped"])
    wrong_unit = dict(base, observation_id=base["observation_id"] + "-wrong-unit", unit="synthetic_unknown_unit", evidence_ids=["syn-ev-wrong-unit"])
    return {
        "request_id": config["request_id"], "synthetic": True, "data_nature": "synthetic_test", "permission_status": PERMISSION,
        "operational_eligible": False,
        "scenarios": [
            {"scenario_id": "missing_month", "input": {"expected_period": base["period_start"], "observation": None}, "expected": "missing_data; do not substitute zero"},
            {"scenario_id": "scope_mismatch", "input": dict(base, geography="JP"), "expected": "exclude or report scope mismatch"},
            {"scenario_id": "revision_vintage", "input": {"original_v1": base, "revised_v2": revision}, "expected": "preserve both versions; latest-vintage choice must be explicit"},
            {"scenario_id": "duplicate_key", "input": {"first": duplicate, "second": duplicate}, "expected": "quality error; do not double count"},
            {"scenario_id": "outlier", "input": outlier, "expected": "flag for review; do not silently clip"},
            {"scenario_id": "permission_unknown", "input": unknown, "expected": "withhold from evidence/findings"},
            {"scenario_id": "unmapped_category", "input": unmapped, "expected": "unclassified/partial; never infer mapping"},
            {"scenario_id": "unknown_unit", "input": wrong_unit, "expected": "reject or partial with invalid unit; do not convert"},
        ],
    }


def _write_request(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps({
        "synthetic": True, "data_nature": "synthetic_test", "operational_eligible": False,
        "request_id": config["request_id"], "planning_request": {
            "schema_version": "1.0", "request_id": config["request_id"],
            "query": "한국 온라인 여성 아우터 합성 거래액 및 지수 시계열의 범위와 연간 변화 확인",
            "context": {"category": config["scope"]["category"], "market": [config["scope"]["geography"]], "channel": [config["scope"]["channel"]]},
        }, "requested_scope": {**config["scope"], "period_start": config["period_start"], "period_months": config["period_months"]},
        "usage_restriction": NOTICE,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_chart(path: Path, periods: list[tuple[int, int]], totals: dict[tuple[int, int], float], scope: dict[str, Any], config: dict[str, Any]) -> None:
    vals = [totals[p] for p in periods]
    lo, hi = min(vals) * 0.92, max(vals) * 1.05
    x0, y0, w, h = 90, 145, 900, 330
    points = " ".join(f"{x0+i*w/(len(vals)-1):.1f},{y0+h-(v-lo)/(hi-lo)*h:.1f}" for i, v in enumerate(vals))
    grid = []
    for frac in (0, .25, .5, .75, 1):
        y = y0 + h * frac
        val = hi - (hi - lo) * frac
        grid.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0+w}" y2="{y:.1f}" stroke="#dce3ec"/><text x="{x0-8}" y="{y+4:.1f}" text-anchor="end">{val:,.0f}</text>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="610" viewBox="0 0 1100 610"><rect width="100%" height="100%" fill="white"/><style>text{{font-family:Arial,sans-serif;fill:#162235}}.title{{font-size:23px;font-weight:bold}}.label{{font-size:14px}}</style><text x="55" y="42" class="title">합성 월별 거래액 시나리오 · 실제 시장 결과 아님</text><text x="55" y="72" class="label">{scope['geography']} · {scope['channel']} · {scope['category']} · {config['period_start']}부터 {len(periods)}개월 · 단위: KRW million</text>{''.join(grid)}<polyline points="{points}" fill="none" stroke="#2865c7" stroke-width="3"/><text x="{x0}" y="{y0+h+28}" class="label">{periods[0][0]}-{periods[0][1]:02d}</text><text x="{x0+w}" y="{y0+h+28}" text-anchor="end" class="label">{periods[-1][0]}-{periods[-1][1]:02d}</text><text x="{x0+w/2}" y="{y0+h+55}" text-anchor="middle" class="label">월별 시점</text><text x="22" y="{y0+h/2}" transform="rotate(-90 22 {y0+h/2})" text-anchor="middle" class="label">가상 거래액 (KRW million)</text><rect x="55" y="535" width="990" height="48" rx="8" fill="#fff1e8"/><text x="72" y="565" class="label">모든 값은 seed와 생성 가정으로 만든 합성값이며 시장 규모·전망·대표성을 뜻하지 않습니다.</text></svg>'''
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, help="Override the seed in the scenario config")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.seed is not None:
        config["seed"] = args.seed
    summary = build(config, args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
