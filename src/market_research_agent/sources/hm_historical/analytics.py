"""Deterministic analytics over the isolated synthetic demo fixture."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .fixtures import _evidence, _source_map, _weeks_for_profile, load_fixture


def _period_sales(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    products = {product["style_id"]: product for product in data["products"]}
    result: dict[str, dict[str, Any]] = {}
    for profile in data["sales_profiles"]:
        product = products[profile["style_id"]]
        units = sum(profile["weekly_units"])
        list_price = product["price_krw"]
        discount_rate = profile["discount_rate"]
        revenue = round(units * list_price * (1 - discount_rate))
        result[profile["style_id"]] = {
            "units": units,
            "revenue_krw": revenue,
            "discount_rate": discount_rate,
            "weekly_average": units / len(profile["weekly_units"]),
            "return_units": profile["return_units"],
            "return_rate": profile["return_units"] / units if units else 0,
            "stockout_days": profile["stockout_days"],
            "channel": profile["channel"],
            "source_id": profile["source_id"],
            "week_count": len(profile["weekly_units"]),
        }
    return result


def analyze() -> dict[str, Any]:
    """Calculate fixture metrics deterministically; never ask a model to do arithmetic."""
    data = load_fixture()
    sources = _source_map(data)
    sales = _period_sales(data)
    inventory = {row["style_id"]: row for row in data["inventory"]}
    products = {row["style_id"]: row for row in data["products"]}
    week_starts = [
        week
        for profile in data["sales_profiles"]
        for week in _weeks_for_profile(profile)
    ]
    start = min(week_starts)
    end = (date.fromisoformat(max(week_starts)) + timedelta(days=6)).isoformat()
    total_units = sum(item["units"] for item in sales.values())
    total_revenue = sum(item["revenue_krw"] for item in sales.values())

    best = max(sales, key=lambda style: sales[style]["units"])
    best_product = products[best]
    best_sales = sales[best]
    opportunities: list[dict[str, Any]] = [{
        "kind": "성과 신호",
        "title": f"판매 수량 상위: {best_product['name']}",
        "summary": f"{best_sales['units']:,} units가 관측됐습니다. {best_product['color']} 색상, {best_product['fit']} 핏 상품입니다.",
        "value": f"{best_sales['units']:,} units",
        "period": f"{start}–{end} · {best_sales['week_count']}주",
        "evidence": [
            _evidence(best_sales["source_id"], f"{best}:units", sources, "12주 구매 수량 합계"),
            _evidence(best_product["source_id"], f"{best}:catalog", sources, "상품 속성"),
        ],
        "caveat": "가상 데이터의 관측 결과이며 다음 시즌 판매 예측이 아닙니다.",
    }]

    stock_rows = []
    for style_id, stock in inventory.items():
        metric = sales.get(style_id)
        if not metric:
            continue
        cover = stock["on_hand_units"] / metric["weekly_average"] if metric["weekly_average"] else None
        stock_rows.append((style_id, stock, metric, cover))
    low_cover = min((row for row in stock_rows if row[3] is not None), key=lambda row: row[3])
    _, low_stock, low_metric, low_cover_weeks = low_cover
    low_product = products[low_cover[0]]
    opportunities.append({
        "kind": "재고 검토 신호",
        "title": f"재고 커버가 짧은 편: {low_product['name']}",
        "summary": f"현재고 {low_stock['on_hand_units']:,} units ÷ 12주 평균 주간 판매 {low_metric['weekly_average']:.1f} units = {low_cover_weeks:.1f}주.",
        "value": f"{low_cover_weeks:.1f}주 커버",
        "period": f"재고 기준일 {sources[low_stock['source_id']]['as_of']} · 판매 {start}–{end}",
        "evidence": [
            _evidence(low_stock["source_id"], f"{low_cover[0]}:inventory", sources, "현재고 스냅샷"),
            _evidence(low_metric["source_id"], f"{low_cover[0]}:weekly-average", sources, "12주 평균 판매"),
            _evidence("SRC-DEMO-KPI-01", "DOC-KPI-01-P3", sources, "커버 주수 정의"),
        ],
        "caveat": "가상 재고 스냅샷 기준의 검토 신호입니다. 입고 예정·품절 이력·발주 판단을 대체하지 않습니다.",
    })
    high_cover = max((row for row in stock_rows if row[3] is not None), key=lambda row: row[3])
    _, high_stock, high_metric, high_cover_weeks = high_cover
    high_product = products[high_cover[0]]
    if high_cover_weeks >= 12:
        opportunities.append({
            "kind": "재고 검토 신호",
            "title": f"재고 커버가 긴 편: {high_product['name']}",
            "summary": f"현재고 {high_stock['on_hand_units']:,} units ÷ 12주 평균 주간 판매 {high_metric['weekly_average']:.1f} units = {high_cover_weeks:.1f}주.",
            "value": f"{high_cover_weeks:.1f}주 커버",
            "period": f"재고 기준일 {sources[high_stock['source_id']]['as_of']} · 판매 {start}–{end}",
            "evidence": [
                _evidence(high_stock["source_id"], f"{high_cover[0]}:inventory", sources, "현재고 스냅샷"),
                _evidence(high_metric["source_id"], f"{high_cover[0]}:weekly-average", sources, "12주 평균 판매"),
                _evidence("SRC-DEMO-KPI-01", "DOC-KPI-01-P3", sources, "커버 주수 정의"),
            ],
            "caveat": "가상 데이터에서 비교적 긴 커버로 표시한 검토 신호입니다. 과잉 재고나 할인 필요를 자동 판정하지 않습니다.",
        })

    feedback_counts: dict[str, list[dict[str, Any]]] = {}
    for row in data["feedback"]:
        if row["rating"] <= 3:
            feedback_counts.setdefault(row["theme"], []).append(row)
    repeated = max(feedback_counts.items(), key=lambda item: len(item[1]))
    theme, feedback_rows = repeated
    themed_product = products[feedback_rows[0]["style_id"]]
    opportunities.append({
        "kind": "고객 의견 신호",
        "title": f"반복 언급된 불만 주제: {theme}",
        "summary": f"낮은 평점(1–3점) {len(feedback_rows)}건에서 같은 주제가 나타났습니다. 예: “{feedback_rows[0]['text']}”",
        "value": f"{len(feedback_rows)}건",
        "period": "2026-01–2026-02 · 가상 리뷰 표본",
        "evidence": [
            _evidence(row["source_id"], row["feedback_id"], sources, f"고객 의견 · {row['date']} · {row['rating']}/5")
            for row in feedback_rows
        ],
        "caveat": f"가상 리뷰 표본이며 대표성 있는 고객 조사로 간주할 수 없습니다. 연결 상품: {themed_product['name']}.",
    })

    present_values = {
        "color": {product["color"].casefold() for product in data["products"]},
        "material": {product["material"].casefold() for product in data["products"]},
        "fit": {product["fit"].casefold() for product in data["products"]},
    }
    for check in data["scope"].get("assortment_checks", []):
        absent = [value for value in check["values"] if value.casefold() not in present_values.get(check["field"], set())]
        if absent and data["scope"].get("catalog_scope_complete"):
            opportunities.append({
                "kind": "구색 검토 가설",
                "title": f"트렌드 검토 {check['field']} 표본 구색에서 미확인: {', '.join(absent)}",
                "summary": f"완전성이 설정된 가상 {data['scope']['category']} 표본 {len(data['products'])}개 Style에서 해당 값이 확인되지 않았습니다.",
                "value": f"{len(absent)}개 값 미포함",
                "period": "상품 기준 26SS 가상 구색 · 트렌드 참고 27SS",
                "evidence": [
                    _evidence(data["scope"]["source_id"], "catalog:scope", sources, "표본 카탈로그 범위"),
                    _evidence(check["source_id"], check["passage_id"], sources, "27SS 색상 탐색 방향"),
                ],
                "caveat": "신규 색상 기회가 확정된 것은 아닙니다. MD가 시즌 적합성·소재/염색 가능성을 검토해야 합니다.",
            })

    table = []
    for style_id, product in products.items():
        metric = sales[style_id]
        stock = inventory[style_id]
        cover = stock["on_hand_units"] / metric["weekly_average"] if metric["weekly_average"] else 0
        table.append({
            **product,
            "units": metric["units"], "revenue_krw": metric["revenue_krw"],
            "discount_rate": metric["discount_rate"], "return_rate": metric["return_rate"],
            "stockout_days": metric["stockout_days"], "channel": metric["channel"],
            "on_hand_units": stock["on_hand_units"], "inbound_units": stock["inbound_units"],
            "lead_time_days": stock["lead_time_days"],
            "cover_weeks": round(cover, 1),
            "evidence": [
                _evidence(metric["source_id"], f"{style_id}:sales", sources, "12주 판매 aggregate"),
                _evidence(stock["source_id"], f"{style_id}:inventory", sources, "현재고 스냅샷"),
                _evidence(product["source_id"], f"{style_id}:catalog", sources, "상품 속성"),
            ],
        })

    total_stockout_days = sum(metric["stockout_days"] for metric in sales.values())
    return {
        "scope": data["scope"],
        "period": {"start": start, "end": end, "weeks": 12},
        "kpis": [
            {"label": "관측 판매 수량", "value": f"{total_units:,}", "unit": "units", "source_ids": ["SRC-DEMO-SALES-01"], "record_id": "sales:category", "period": f"{start}–{end}"},
            {"label": "계산 매출 참고값", "value": f"₩{total_revenue:,.0f}", "unit": "KRW (synthetic estimate)", "source_ids": ["SRC-DEMO-SALES-01", "SRC-DEMO-CATALOG-01"], "record_id": "sales:category", "period": f"{start}–{end}"},
            {"label": "가중 평균 할인율", "value": f"{sum(x['discount_rate'] * x['units'] for x in sales.values()) / total_units:.1%}", "unit": "sales-weighted ratio", "source_ids": ["SRC-DEMO-SALES-01"], "record_id": "sales:category", "period": f"{start}–{end}"},
            {"label": "판매 기록된 품절 일수", "value": str(total_stockout_days), "unit": "style-days (sample field)", "source_ids": ["SRC-DEMO-SALES-01"], "record_id": "sales:category", "period": f"{start}–{end}"},
        ],
        "opportunities": opportunities,
        "products": table,
        "sources": list(sources.values()),
        "feedback": data["feedback"],
        "documents": data["documents"],
        "fixture_notice": "모든 상품·판매·재고·고객 의견·문서는 합성 시연 자료입니다. 실제 회사 데이터와 혼합하지 마세요.",
        "analysis_notes": [
            "매출 참고값은 합성 MSRP × (1 − 합성 할인율) × units 계산입니다. 원천 매출이 아닙니다.",
            "반품률은 합성 return units ÷ 합성 판매 units이며 실제 KPI 정의가 아닙니다.",
            "재고 커버는 현재고 ÷ 12주 평균 판매로 계산한 탐색용 값입니다.",
            "판매 집계의 채널은 합성 데이터의 표시명이며 실제 채널 코드 체계가 아닙니다.",
        ],
    }
