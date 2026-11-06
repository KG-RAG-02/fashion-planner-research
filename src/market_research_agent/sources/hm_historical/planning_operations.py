"""Read-only access to planning/operations demo data and local H&M catalog."""

from __future__ import annotations

import csv
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DEFAULT_HM_ANALYSIS_PATH, ROOT

FIXTURE_PATH = ROOT / "examples" / "fixtures" / "planning_operations_demo.json"
DEMO_FIXTURE_PATH = ROOT / "examples" / "fixtures" / "prototype_demo.json"


def load_fixture() -> dict[str, Any]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    validate_fixture(data)
    return data


def _valid_date_range(start: str, end: str) -> bool:
    try:
        return date.fromisoformat(start) <= date.fromisoformat(end)
    except (TypeError, ValueError):
        return False


def validate_fixture(data: dict[str, Any]) -> None:
    required_metadata = {"schema_version", "data_status", "source_id", "version", "as_of", "scope", "limitations"}
    if required_metadata - data.keys() or data.get("data_status") != "synthetic":
        raise ValueError("Planning operations fixture must declare synthetic provenance and dataset metadata.")
    for collection in ("line_plans", "markdown_reviews", "kpi_definitions", "product_details", "campaigns"):
        if not isinstance(data.get(collection), list):
            raise ValueError(f"Planning operations fixture collection is invalid: {collection}")
        for record in data[collection]:
            if record.get("data_status") != "synthetic" or not record.get("source_id") or not record.get("version") or not record.get("as_of") or not record.get("limitations"):
                raise ValueError(f"Record in {collection} is missing synthetic provenance metadata.")

    demo = json.loads(DEMO_FIXTURE_PATH.read_text(encoding="utf-8"))
    demo_products = {row["style_id"]: row for row in demo.get("products", [])}
    for plan in data["line_plans"]:
        if not plan.get("season_id") or not plan.get("season_label") or not _valid_date_range(plan.get("valid_from", ""), plan.get("valid_to", "")):
            raise ValueError("Line plan season or validity period is invalid.")
        groups = plan.get("product_groups", [])
        if not groups or len({row.get("product_group_id") for row in groups}) != len(groups):
            raise ValueError("Line plan product groups must be non-empty and uniquely identified.")
        if abs(sum(float(row["target_assortment_share"]) for row in groups) - 1) > 1e-6:
            raise ValueError("Line plan assortment shares must sum to 1.")
        for group in groups:
            if int(group.get("target_style_count", 0)) <= 0:
                raise ValueError("Line plan style count must be positive.")
            tiers = group.get("price_ladder", [])
            if tiers and abs(sum(float(tier["target_share"]) for tier in tiers) - 1) > 1e-6:
                raise ValueError("Price ladder shares must sum to 1.")
            for tier in tiers:
                if int(tier.get("min_price", 0)) <= 0 or int(tier.get("max_price", 0)) < int(tier.get("min_price", 0)):
                    raise ValueError("Illustrative price bracket is invalid.")
            if tiers and any(int(tiers[index]["max_price"]) >= int(tiers[index + 1]["min_price"]) for index in range(len(tiers) - 1)):
                raise ValueError("Illustrative price tiers must be ordered and non-overlapping.")
            if not set(group.get("reference_style_ids", [])) <= set(demo_products):
                raise ValueError("Line plan reference styles must resolve to the synthetic product fixture.")

    for markdown in data["markdown_reviews"]:
        product = demo_products.get(markdown.get("style_id"))
        if product is None:
            raise ValueError("Markdown scenario must reference a known synthetic demo style.")
        if markdown.get("list_price") != product.get("price_krw"):
            raise ValueError("Markdown scenario list price must match its synthetic catalog record.")
        if not (0 < markdown.get("markdown_price", 0) < markdown.get("list_price", 0)):
            raise ValueError("Markdown scenario price must be positive and below list price.")
        if not _valid_date_range(markdown.get("effective_from", ""), markdown.get("effective_to", "")):
            raise ValueError("Markdown scenario effective dates are invalid.")

    for definition in data["kpi_definitions"]:
        if definition.get("measurement_status") == "not_connected" and definition.get("measurement_value") is not None:
            raise ValueError("Unconnected KPI measurements must not contain a value.")
        target = definition.get("illustrative_target")
        if target is not None and float(target) < 0:
            raise ValueError("Illustrative KPI target cannot be negative.")

    for product in data["product_details"]:
        if product.get("style_id") not in demo_products or not product.get("season_id") or not product.get("scope", {}).get("season"):
            raise ValueError("Product detail must reference a known demo product and an explicit source season.")
        shares = [item.get("share_percent") for item in product.get("material_composition", [])]
        if shares and all(value is not None for value in shares) and abs(sum(float(value) for value in shares) - 100) > 1e-6:
            raise ValueError("Known material composition shares must sum to 100 percent.")

    known_styles = set(demo_products) | {row.get("style_id") for row in data["product_details"]}
    for campaign in data["campaigns"]:
        if not _valid_date_range(campaign.get("start_date", ""), campaign.get("end_date", "")):
            raise ValueError("Campaign period is invalid.")
        if not set(campaign.get("product_style_ids", [])) <= known_styles:
            raise ValueError("Campaign references an unknown product style.")
        if campaign.get("measurement_status") == "not_connected" and any(value is not None for value in campaign.get("metrics", {}).values()):
            raise ValueError("Unconnected campaign outcome metrics must remain null.")


def list_records(collection: str) -> dict[str, Any]:
    data = load_fixture()
    if collection not in {"line_plans", "markdown_reviews", "kpi_definitions", "campaigns"}:
        raise ValueError("Unknown planning operations collection.")
    return {
        "schema_version": data["schema_version"], "data_status": "synthetic",
        "source_id": data["source_id"], "version": data["version"], "as_of": data["as_of"],
        "scope": data["scope"], "limitations": data["limitations"], "records": data[collection],
    }


def get_data_status() -> dict[str, Any]:
    data = load_fixture()
    return {
        "as_of": data["as_of"],
        "datasets": [
            {"dataset": "hm_historical_catalog_and_sales", "data_status": "observed", "source_id": ["KAGGLE-HM-ARTICLES", "KAGGLE-HM-TRANSACTIONS"], "local_path": "data/processed/hm-personalized-fashion-recommendations/", "period": "2018-09-20–2020-09-22", "limitation": "Historical public competition data; not live internal data."},
            {"dataset": "season_line_plans", "data_status": "synthetic", "source_id": "SRC-SYN-PLANNING-OPS-01", "version": data["version"], "limitation": "Illustrative line plan only."},
            {"dataset": "markdown_reviews", "data_status": "synthetic", "source_id": "SRC-SYN-MARKDOWN-01", "version": data["version"], "limitation": "No actual markdown review or effect data."},
            {"dataset": "approved_targets_and_kpi_measurements", "data_status": "not_connected", "source_id": None, "limitation": "Synthetic KPI definitions exist for UI validation; approved target and measured values are not connected."},
            {"dataset": "product_material_care_size_details", "data_status": "synthetic", "source_id": "SRC-SYN-PRODUCT-01", "version": data["version"], "limitation": "Detailed material, care, fit, and size attributes are synthetic; Kaggle catalog descriptions are available separately."},
            {"dataset": "integrated_synthetic_merchandising_scenario", "data_status": "synthetic", "source_id": "SRC-SYN-MERCH-01", "version": "synthetic-merch-generator-1.0", "local_path": "examples/fixtures/synthetic_merch_scenario.json", "limitation": "Deterministic isolated scenario across plan, products, sales, stock, markdown, KPI, campaign, and feedback; not H&M or market evidence."},
            {"dataset": "fashion_cloud_api", "data_status": "not_connected", "source_id": "FASHION-CLOUD-OPENAPI-V2", "local_path": "data/raw/fashion-cloud-api-v2.json", "limitation": "Local OpenAPI schema only; account approval and credentials are absent and no API requests are made."},
            {"dataset": "campaign_outcomes", "data_status": "not_connected", "source_id": None, "limitation": "No authorized analytics/ad account or mapped campaign outcomes."},
            *[{"dataset": row["system"], **row} for row in data["integrations"]],
        ],
    }


def list_products() -> dict[str, Any]:
    data = load_fixture()
    return {"data_status": "synthetic", "source_id": "SRC-SYN-PRODUCT-01", "version": data["version"], "as_of": data["as_of"], "scope": data["scope"], "limitations": data["limitations"], "products": data["product_details"]}


def _synthetic_product(style_id: str) -> dict[str, Any] | None:
    data = load_fixture()
    return next((record for record in data["product_details"] if record.get("style_id") == style_id), None)


@lru_cache(maxsize=1)
def _catalog_rows() -> tuple[list[dict[str, str]], str | None]:
    catalog_path = DEFAULT_HM_ANALYSIS_PATH.with_name("article_catalog.csv")
    if not catalog_path.is_file():
        return [], None
    with catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream)), str(catalog_path.relative_to(ROOT))


def get_product(style_or_article_id: str) -> dict[str, Any] | None:
    product = _synthetic_product(style_or_article_id)
    if product:
        return {"data_status": "synthetic", "product": product}
    rows, local_path = _catalog_rows()
    matches = [row for row in rows if row.get("article_id") == style_or_article_id or row.get("product_code") == style_or_article_id]
    if not matches:
        return None
    row = matches[0]
    report_path = DEFAULT_HM_ANALYSIS_PATH
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    return {
        "data_status": "observed", "source_id": "KAGGLE-HM-ARTICLES",
        "version": report.get("analysis_version", "unknown"), "as_of": report.get("generated_at_utc"),
        "scope": {"season": None, "market": None, "channel": None},
        "limitations": ["Historical catalog snapshot from the public Kaggle competition; not a live H&M PIM feed.", "The source provides no validated material composition, care instructions, fit notes, or size guide."],
        "source_file": local_path, "matched_article_count": len(matches), "product": row,
    }


def calculate_ratio_kpi(numerator: float | None, denominator: float | None, missing_fields: list[str] | None = None) -> dict[str, Any]:
    """Calculate a percentage only when both observed inputs and denominator exist."""
    missing = list(missing_fields or [])
    if numerator is None:
        missing.append("numerator")
    if denominator is None:
        missing.append("denominator")
    if missing or denominator == 0:
        if denominator == 0:
            missing.append("non_zero_denominator")
        return {"status": "not_calculable", "value": None, "unit": "percent", "missing_fields": list(dict.fromkeys(missing))}
    return {"status": "calculated", "value": round(float(numerator) / float(denominator) * 100, 4), "unit": "percent", "missing_fields": []}
