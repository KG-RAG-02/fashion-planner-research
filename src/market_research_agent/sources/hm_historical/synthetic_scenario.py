"""Deterministic, explicitly synthetic merchandising scenario and analyses.

This module never reads or joins the H&M Kaggle catalog or transactions.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import ROOT

SEED = 26061105
GENERATOR_VERSION = "synthetic-merch-generator-1.0"
OUTPUT = ROOT / "examples" / "fixtures" / "synthetic_merch_scenario.json"
AS_OF = "2026-11-05T00:00:00Z"
LIMITS = [
    "All records are generated for software and analysis demonstrations; not H&M or market facts.",
    "Generated sales, stock, prices, targets, campaign outcomes, and feedback are synthetic and must not be combined with observed Kaggle data.",
    "Before/after comparisons are descriptive scenarios and do not estimate causal effects.",
]


def build_scenario(seed: int = SEED) -> dict[str, Any]:
    rng = random.Random(seed)
    season_id, season, market, currency = "SYN-27SS", "27SS", "KR-DEMO", "KRW"
    weeks = [date(2027, 1, 4) + timedelta(days=7 * i) for i in range(12)]
    groups = [
        {"id": "SYN-GRP-OUTER", "name": "Light Outerwear", "style_count": 4, "share": .4, "prices": [89900, 109900, 149900], "colors": ["SYN-Sage", "SYN-Navy"]},
        {"id": "SYN-GRP-KNIT", "name": "Knitwear", "style_count": 3, "share": .3, "prices": [49900, 79900, 109900], "colors": ["SYN-Oat", "SYN-Blue"]},
        {"id": "SYN-GRP-BOTTOM", "name": "Bottoms", "style_count": 3, "share": .3, "prices": [59900, 89900, 129900], "colors": ["SYN-Black", "SYN-Stone"]},
    ]
    source = {"data_status": "synthetic", "source_id": "SRC-SYN-MERCH-01", "version": GENERATOR_VERSION, "as_of": AS_OF, "seed": seed, "generation_rules": "Fixed seed; style baseline and week demand are generated from the documented deterministic rules in synthetic_scenario.py.", "limitations": LIMITS}
    record_meta = {key: source[key] for key in ("data_status", "source_id", "version", "as_of", "seed")}
    record_meta["generation_rules"] = "See dataset generation_rules; deterministic from the recorded seed."
    record_meta["limitations"] = LIMITS
    line_plan_groups = []
    for group in groups:
        line_plan_groups.append({"product_group_id": group["id"], "product_group_name": group["name"], "target_style_count": group["style_count"], "target_assortment_share": group["share"], "price_ladder": [{"tier": "entry", "min_price": min(group["prices"]), "max_price": min(group["prices"]), "target_share": .3}, {"tier": "core", "min_price": group["prices"][1], "max_price": group["prices"][1], "target_share": .5}, {"tier": "premium", "min_price": max(group["prices"]), "max_price": max(group["prices"]), "target_share": .2}], "principles": ["Synthetic assortment balance scenario"]})
    line_plan = {**record_meta, "line_plan_id": "SYN-LP-27SS-01", "season_id": season_id, "season": season, "market": market, "currency": currency, "valid_from": "2027-01-01", "valid_to": "2027-06-30", "plan_status": "synthetic_draft", "groups": line_plan_groups}

    styles, articles, skus, details = [], [], [], []
    style_index = 0
    for group in groups:
        for _ in range(group["style_count"]):
            style_index += 1
            style_id = f"SYN-STYLE-{style_index:03d}"
            price = group["prices"][(style_index - 1) % len(group["prices"])]
            styles.append({**record_meta, "style_id": style_id, "product_group_id": group["id"], "product_name": f"{group['name']} concept {style_index:02d}", "season_id": season_id, "data_status": "synthetic"})
            comp_main = 100 if style_index % 2 else 92
            details.append({**record_meta, "style_id": style_id, "description": "Synthetic concept description for data-flow validation.", "material_composition": [{"material": "SYN-Recycled polyester", "share_percent": comp_main}, *([] if comp_main == 100 else [{"material": "SYN-Elastane", "share_percent": 8}])], "care_instructions": ["SYN: machine wash scenario; validate against real care label before use"], "fit": "SYN-regular" if style_index % 2 else "SYN-relaxed", "size_system": "SYN-alpha", "size_measurements": [{"size": size, "chest_cm": round(84 + i * 8 + rng.uniform(-1, 1), 1)} for i, size in enumerate(["S", "M", "L"])], "limitations": LIMITS})
            for color_i, color in enumerate(group["colors"], 1):
                article_id = f"SYN-ARTICLE-{style_index:03d}-{color_i:02d}"
                articles.append({**record_meta, "article_id": article_id, "style_id": style_id, "color_id": color, "color_name": color.removeprefix("SYN-"), "list_price": price, "currency": currency, "season_id": season_id, "data_status": "synthetic"})
                for size_i, size in enumerate(["S", "M", "L"], 1):
                    skus.append({**record_meta, "sku_id": f"SYN-SKU-{style_index:03d}-{color_i:02d}-{size_i:02d}", "article_id": article_id, "size": size, "data_status": "synthetic"})

    sku_to_article = {x["sku_id"]: x["article_id"] for x in skus}
    article_by_id = {x["article_id"]: x for x in articles}
    sales, inventory = [], []
    style_by_article = {x["article_id"]: x["style_id"] for x in articles}
    opening = {x["sku_id"]: (rng.randint(30, 45) if style_by_article[x["article_id"]] == "SYN-STYLE-001" else rng.randint(80, 130)) for x in skus}
    for week_index, week_start in enumerate(weeks):
        for sku in skus:
            sku_id = sku["sku_id"]
            article = article_by_id[sku_to_article[sku_id]]
            style_num = int(article["style_id"].split("-")[-1])
            baseline = 5 + (style_num % 5) + (1 if sku["size"] == "M" else 0)
            demand = max(0, round(baseline * (1 + .18 * ((week_index % 4) / 3)) + rng.uniform(-2, 3)))
            received = rng.randint(8, 18) if week_index in (0, 4, 8) else 0
            adjustment = max(-opening[sku_id] - received, rng.choice([-1, 0, 0, 0, 1]))
            available = max(0, opening[sku_id] + received + adjustment)
            units = min(demand, available)
            price = article["list_price"]
            if week_index >= 8 and style_num == 1:
                price = round(price * .8 / 100) * 100
            sales.append({**record_meta, "sku_id": sku_id, "article_id": article["article_id"], "style_id": article["style_id"], "week_start": week_start.isoformat(), "channel": "SYN-ONLINE" if (style_num + week_index) % 2 else "SYN-STORE", "demand_units": demand, "units_sold": units, "selling_price": price, "currency": currency, "promotion_id": "SYN-PROMO-27SS-01" if week_index in (5, 6) else None})
            closing = available - units
            inventory.append({**record_meta, "sku_id": sku_id, "article_id": article["article_id"], "style_id": article["style_id"], "week_start": week_start.isoformat(), "opening_units": opening[sku_id], "receipt_units": received, "units_sold": units, "adjustment_units": adjustment, "closing_units": closing, "stockout": closing == 0, "inventory_status": "synthetic_scenario_balance"})
            opening[sku_id] = closing

    markdown = {**record_meta, "event_id": "SYN-MD-27SS-01", "style_id": "SYN-STYLE-001", "start_week": weeks[8].isoformat(), "end_week": weeks[11].isoformat(), "list_price": 89900, "markdown_price": 71900, "currency": currency, "reason": "SYN-end-of-season scenario", "comparison_weeks_before": 4, "comparison_weeks_after": 4, "causal_effect_claim": False}
    campaign_sales = [row for row in sales if row["promotion_id"] == "SYN-PROMO-27SS-01" and row["channel"] == "SYN-ONLINE"]
    campaign_purchases = sum(row["units_sold"] for row in campaign_sales)
    campaign_revenue = sum(row["units_sold"] * row["selling_price"] for row in campaign_sales)
    campaign = {**record_meta, "campaign_id": "SYN-CAMPAIGN-27SS-01", "promotion_id": "SYN-PROMO-27SS-01", "channel": "SYN-ONLINE", "style_ids": sorted({row["style_id"] for row in campaign_sales}), "start_week": weeks[5].isoformat(), "end_week": weeks[6].isoformat(), "attribution_window_days": 7, "impressions": 12500, "clicks": 680, "items_viewed": 1050, "items_purchased": campaign_purchases, "item_revenue": campaign_revenue, "spend": 12000000, "currency": currency, "limitations": [*LIMITS, "Campaign impressions, clicks, and spend are synthetic counters; purchases and revenue sum synthetic promoted sales rows and do not establish incrementality."]}
    feedback = [{**record_meta, "response_id": f"SYN-FEEDBACK-{i:03d}", "style_id": styles[(i - 1) % len(styles)]["style_id"], "week": weeks[(i - 1) % len(weeks)].isoformat(), "rating": 2 + (i % 4), "theme": ["SYN-packability", "SYN-fit", "SYN-care"][i % 3], "comment": "Synthetic feedback text; invented only to exercise theme aggregation."} for i in range(1, 25)]
    definitions = [
        {**record_meta, "kpi_id": "SYN-KPI-SELL-THROUGH", "name": "Scenario sell-through", "formula": "units_sold / (opening_units + receipts + adjustments) * 100", "unit": "percent", "grain": "style × season × market × channel", "target": 68.0},
        {**record_meta, "kpi_id": "SYN-KPI-MARKDOWN-RATE", "name": "Scenario markdown rate", "formula": "(list_price - selling_price) / list_price * 100", "unit": "percent", "grain": "style × week × market × channel", "target": None},
        {**record_meta, "kpi_id": "SYN-KPI-PROMO-CTR", "name": "Scenario promotion CTR", "formula": "clicks / impressions * 100", "unit": "percent", "grain": "campaign × channel", "target": 5.0},
        {**record_meta, "kpi_id": "SYN-KPI-PROMO-ROAS", "name": "Scenario promotion ROAS", "formula": "item_revenue / spend", "unit": "ratio", "grain": "campaign × channel", "target": 4.0},
    ]
    sold = sum(x["units_sold"] for x in sales)
    first_week = min(row["week_start"] for row in inventory)
    denominator = sum(row["opening_units"] for row in inventory if row["week_start"] == first_week)
    denominator += sum(row["receipt_units"] + row["adjustment_units"] for row in inventory)
    kpi_results = [{**record_meta, "kpi_id": "SYN-KPI-SELL-THROUGH", "value": round(sold / denominator * 100, 2), "unit": "percent", "numerator": sold, "denominator": denominator}]
    kpi_results.append({**record_meta, "kpi_id": "SYN-KPI-MARKDOWN-RATE", "value": round((89900 - 71900) / 89900 * 100, 2), "unit": "percent", "numerator": 18000, "denominator": 89900})
    kpi_results.extend([{**record_meta, "kpi_id": "SYN-KPI-PROMO-CTR", "value": round(campaign["clicks"] / campaign["impressions"] * 100, 2), "unit": "percent", "numerator": campaign["clicks"], "denominator": campaign["impressions"]}, {**record_meta, "kpi_id": "SYN-KPI-PROMO-ROAS", "value": round(campaign["item_revenue"] / campaign["spend"], 2), "unit": "ratio", "numerator": campaign["item_revenue"], "denominator": campaign["spend"]}])
    return {"schema_version": "synthetic-merch-scenario-1", **source, "scope": {"season_id": season_id, "season": season, "market": market, "currency": currency, "week_start": "Monday", "weeks": len(weeks)}, "line_plan": line_plan, "styles": styles, "articles": articles, "skus": skus, "product_details": details, "sales": sales, "inventory": inventory, "markdown_events": [markdown], "kpi_definitions": definitions, "kpi_results": kpi_results, "campaigns": [campaign], "feedback": feedback}


def analyze_scenario(data: dict[str, Any]) -> dict[str, Any]:
    sales, inventory = data["sales"], data["inventory"]
    group_names = {x["product_group_id"]: x["product_group_name"] for x in data["line_plan"]["groups"]}
    units_by_style: dict[str, int] = {}
    revenue_by_style: dict[str, int] = {}
    for row in sales:
        units_by_style[row["style_id"]] = units_by_style.get(row["style_id"], 0) + row["units_sold"]
        revenue_by_style[row["style_id"]] = revenue_by_style.get(row["style_id"], 0) + row["units_sold"] * row["selling_price"]
    sold_total = sum(x["units_sold"] for x in sales)
    revenue_total = sum(x["units_sold"] * x["selling_price"] for x in sales)
    groups = []
    price_tiers = []
    for group_id, name in group_names.items():
        style_ids = [x["style_id"] for x in data["styles"] if x["product_group_id"] == group_id]
        groups.append({"product_group": name, "planned_styles": next(x["target_style_count"] for x in data["line_plan"]["groups"] if x["product_group_id"] == group_id), "generated_styles": len(style_ids), "units_sold": sum(units_by_style.get(s, 0) for s in style_ids), "synthetic_revenue": sum(revenue_by_style.get(s, 0) for s in style_ids), "data_status": "synthetic"})
        group_plan = next(x for x in data["line_plan"]["groups"] if x["product_group_id"] == group_id)
        article_prices = {row["style_id"]: row["list_price"] for row in data["articles"] if row["style_id"] in style_ids}
        for tier in group_plan["price_ladder"]:
            tier_styles = [style_id for style_id, price in article_prices.items() if tier["min_price"] <= price <= tier["max_price"]]
            price_tiers.append({"product_group": name, "tier": tier["tier"], "planned_share": tier["target_share"], "planned_price_min": tier["min_price"], "planned_price_max": tier["max_price"], "generated_styles": len(tier_styles), "generated_share": round(len(tier_styles) / len(style_ids), 4) if style_ids else None, "data_status": "synthetic"})
    markdown = data["markdown_events"][0]
    start, end = date.fromisoformat(markdown["start_week"]), date.fromisoformat(markdown["end_week"])
    before_start, after_end = start - timedelta(days=28), end + timedelta(days=21)
    style_sales = [x for x in sales if x["style_id"] == markdown["style_id"]]
    before = sum(x["units_sold"] for x in style_sales if before_start <= date.fromisoformat(x["week_start"]) < start)
    after = sum(x["units_sold"] for x in style_sales if start <= date.fromisoformat(x["week_start"]) <= after_end)
    kpi_by_id = {row["kpi_id"]: row for row in data["kpi_results"]}
    kpi_summary = [{**definition, "measurement": kpi_by_id.get(definition["kpi_id"]), "variance_to_target": round(kpi_by_id[definition["kpi_id"]]["value"] - definition["target"], 2) if definition["target"] is not None and definition["kpi_id"] in kpi_by_id else None} for definition in data["kpi_definitions"]]
    by_week: dict[str, dict[str, int]] = {}
    by_channel: dict[str, dict[str, int]] = {}
    for row in sales:
        weekly = by_week.setdefault(row["week_start"], {"units_sold": 0, "synthetic_revenue": 0})
        weekly["units_sold"] += row["units_sold"]
        weekly["synthetic_revenue"] += row["units_sold"] * row["selling_price"]
        channel = by_channel.setdefault(row["channel"], {"units_sold": 0, "synthetic_revenue": 0, "sales_rows": 0})
        channel["units_sold"] += row["units_sold"]
        channel["synthetic_revenue"] += row["units_sold"] * row["selling_price"]
        channel["sales_rows"] += 1
    return {"data_status": "synthetic", "source_id": data["source_id"], "version": data["version"], "seed": data["seed"], "scope": data["scope"], "period": {"start": min(x["week_start"] for x in sales), "end": max(x["week_start"] for x in sales)}, "counts": {"line_plans": 1, "styles": len(data["styles"]), "articles": len(data["articles"]), "skus": len(data["skus"]), "sales_rows": len(sales), "inventory_rows": len(inventory), "markdown_events": len(data["markdown_events"]), "kpi_definitions": len(data["kpi_definitions"]), "campaigns": len(data["campaigns"]), "feedback_records": len(data["feedback"])}, "totals": {"planned_styles": sum(x["target_style_count"] for x in data["line_plan"]["groups"]), "generated_styles": len(data["styles"]), "units_sold": sold_total, "synthetic_revenue": revenue_total, "stockout_sku_weeks": sum(bool(x["stockout"]) for x in inventory)}, "by_product_group": groups, "price_tier_analysis": price_tiers, "by_week": [{"week_start": key, **value} for key, value in sorted(by_week.items())], "by_channel": [{"channel": key, **value} for key, value in sorted(by_channel.items())], "kpi_summary": kpi_summary, "markdown_comparison": {"style_id": markdown["style_id"], "before_units": before, "after_units": after, "descriptive_change_units": after - before, "causal_effect_claim": False, "limitations": ["Descriptive comparison only; stock, channel, trend, and exposure may differ."]}, "campaign_metrics": data["kpi_results"][2:], "feedback_theme_counts": _feedback_counts(data["feedback"]), "limitations": LIMITS}


def _feedback_counts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in records:
        counts[row["theme"]] = counts.get(row["theme"], 0) + 1
    return [{"theme": key, "responses": value, "data_status": "synthetic"} for key, value in sorted(counts.items())]


def validate_scenario(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("data_status") != "synthetic" or not str(data.get("source_id", "")).startswith("SRC-SYN-"):
        errors.append("synthetic provenance metadata is required")
    record_groups = ["styles", "articles", "skus", "product_details", "sales", "inventory", "markdown_events", "kpi_definitions", "kpi_results", "campaigns", "feedback"]
    for name in record_groups:
        for row in data.get(name, []):
            if any(not row.get(field) for field in ("source_id", "version", "as_of", "generation_rules", "limitations")) or row.get("data_status") != "synthetic":
                errors.append(f"synthetic provenance metadata missing in {name}")
                break
    styles = {x["style_id"] for x in data["styles"]}
    articles = {x["article_id"] for x in data["articles"]}
    skus = {x["sku_id"] for x in data["skus"]}
    if any(style_id not in styles for campaign in data.get("campaigns", []) for style_id in campaign.get("style_ids", [])):
        errors.append("campaign style references must resolve")
    if len(styles) != len(data["styles"]) or len(articles) != len(data["articles"]) or len(skus) != len(data["skus"]):
        errors.append("synthetic identifiers must be unique")
    if any(x["style_id"] not in styles for x in data["articles"]) or any(x["article_id"] not in articles for x in data["skus"]):
        errors.append("style/article/SKU references must resolve")
    for row in data["inventory"]:
        if row["opening_units"] + row["receipt_units"] + row["adjustment_units"] - row["units_sold"] != row["closing_units"]:
            errors.append("inventory balance equation failed")
            break
    if len({(x["sku_id"], x["week_start"]) for x in data["inventory"]}) != len(data["inventory"]) or any(x["sku_id"] not in skus for x in data["sales"]):
        errors.append("weekly inventory/sales grain or SKU references invalid")
    if any(x["units_sold"] < 0 or x["selling_price"] <= 0 for x in data["sales"]):
        errors.append("sales units/prices must be valid")
    if abs(sum(x["target_assortment_share"] for x in data["line_plan"]["groups"]) - 1) > 1e-6:
        errors.append("line plan group shares must sum to 1")
    for item in data["product_details"]:
        if abs(sum(x["share_percent"] for x in item["material_composition"]) - 100) > 1e-6:
            errors.append("synthetic material shares must sum to 100")
            break
    return errors


def write_fixture(path: Path = OUTPUT, seed: int = SEED) -> dict[str, Any]:
    scenario = build_scenario(seed)
    errors = validate_scenario(scenario)
    if errors:
        raise ValueError("; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return analyze_scenario(scenario)


def load_scenario() -> dict[str, Any]:
    data = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.is_file() else build_scenario()
    errors = validate_scenario(data)
    if errors:
        raise ValueError("; ".join(errors))
    return data


if __name__ == "__main__":
    print(json.dumps(write_fixture(), ensure_ascii=False, indent=2))
