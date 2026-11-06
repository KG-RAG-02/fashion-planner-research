"""Quality audit for processed H&M Kaggle inputs and derived aggregates."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/processed/hm-personalized-fashion-recommendations")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def audit(processed_dir: Path = DEFAULT_DIR) -> dict[str, Any]:
    """Audit current outputs without reading or exporting customer-level data."""
    root = processed_dir.expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    catalog_fields, catalog = _read_csv(root / "article_catalog.csv")
    weekly_fields, weekly = _read_csv(root / "weekly_sales_by_article_channel.csv")
    style_fields, styles = _read_csv(root / "style_sales_summary.csv")

    required_catalog = {"article_id", "product_code", "product_group_name", "product_type_name", "colour_group_name"}
    required_weekly = {"week_start", "article_id", "sales_channel_id", "purchase_rows", "price_sum_source_units", "matched_article"}
    missing = {
        "article_catalog.csv": sorted(required_catalog - set(catalog_fields)),
        "weekly_sales_by_article_channel.csv": sorted(required_weekly - set(weekly_fields)),
    }
    if any(missing.values()):
        raise ValueError(f"Required columns missing: {missing}")

    catalog_by_id: dict[str, dict[str, str]] = {}
    duplicate_catalog_ids: Counter[str] = Counter()
    blank_catalog_ids = 0
    missing_by_column: Counter[str] = Counter()
    product_code_types: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in catalog:
        article_id = row["article_id"].strip()
        if not article_id:
            blank_catalog_ids += 1
            continue
        if article_id in catalog_by_id:
            duplicate_catalog_ids[article_id] += 1
        catalog_by_id[article_id] = row
        for field in catalog_fields:
            if not (row.get(field) or "").strip():
                missing_by_column[field] += 1
        product_code = (row.get("product_code") or "").strip()
        product_code_types[product_code].add((
            (row.get("product_group_name") or "").strip() or "(unknown)",
            (row.get("product_type_name") or "").strip() or "(unknown)",
        ))

    weekly_key_counts: Counter[tuple[str, str, str]] = Counter()
    weekly_rows = 0
    purchase_rows = 0
    matched_false_rows = 0
    unmatched_rows = 0
    malformed_numeric_rows = 0
    negative_units_rows = 0
    earliest_week: str | None = None
    latest_week: str | None = None
    category_units: Counter[tuple[str, str]] = Counter()
    style_category_units: Counter[tuple[str, str, str]] = Counter()
    exact_category_styles: dict[tuple[str, str], set[str]] = defaultdict(set)
    price_min: float | None = None
    price_max: float | None = None

    for row in weekly:
        weekly_rows += 1
        week = row["week_start"]
        article_id = row["article_id"].strip()
        channel = row["sales_channel_id"].strip()
        weekly_key_counts[(week, article_id, channel)] += 1
        earliest_week = week if earliest_week is None or week < earliest_week else earliest_week
        latest_week = week if latest_week is None or week > latest_week else latest_week
        try:
            units = int(row["purchase_rows"])
            price_sum = float(row["price_sum_source_units"])
            if units < 0:
                negative_units_rows += 1
            if price_sum < 0:
                malformed_numeric_rows += 1
                continue
            unit_price = price_sum / units if units else 0.0
            price_min = unit_price if price_min is None else min(price_min, unit_price)
            price_max = unit_price if price_max is None else max(price_max, unit_price)
        except (ValueError, TypeError, ZeroDivisionError):
            malformed_numeric_rows += 1
            continue
        purchase_rows += units
        metadata = catalog_by_id.get(article_id)
        is_matched = (row.get("matched_article") or "").strip().lower() == "true"
        if not is_matched:
            matched_false_rows += 1
        if metadata is None:
            unmatched_rows += 1
            continue
        group = (metadata.get("product_group_name") or "").strip() or "(unknown)"
        product_type = (metadata.get("product_type_name") or "").strip() or "(unknown)"
        product_code = (metadata.get("product_code") or "").strip() or "(unknown)"
        category_units[(group, product_type)] += units
        style_category_units[(product_code, group, product_type)] += units
        exact_category_styles[(group, product_type)].add(product_code)

    adapter_category_units: Counter[tuple[str, str]] = Counter()
    style_category_conflicts = 0
    style_fields_present = {"product_code", "product_group_name", "product_type_name", "purchase_rows"} <= set(style_fields)
    if style_fields_present:
        for row in styles:
            try:
                units = int(row["purchase_rows"])
            except (ValueError, TypeError):
                continue
            group = row["product_group_name"].strip() or "(unknown)"
            product_type = row["product_type_name"].strip() or "(unknown)"
            adapter_category_units[(group, product_type)] += units
            types = product_code_types.get(row["product_code"].strip(), set())
            if len(types) > 1:
                style_category_conflicts += 1

    all_categories = set(category_units) | set(adapter_category_units)
    mismatches = []
    for key in all_categories:
        exact = category_units[key]
        adapted = adapter_category_units[key]
        if exact != adapted:
            mismatches.append({
                "product_group_name": key[0],
                "product_type_name": key[1],
                "article_level_purchase_rows": exact,
                "style_representative_purchase_rows": adapted,
                "difference": adapted - exact,
            })
    mismatches.sort(key=lambda item: abs(item["difference"]), reverse=True)

    report = {
        "audit_version": "1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "processed_dir": str(root),
        "source_observation_period": manifest.get("observation_period", {}),
        "schema": {
            "article_catalog_columns": catalog_fields,
            "weekly_aggregate_columns": weekly_fields,
            "required_column_issues": missing,
        },
        "catalog_quality": {
            "rows": len(catalog),
            "blank_article_id_rows": blank_catalog_ids,
            "duplicate_article_id_count": sum(duplicate_catalog_ids.values()),
            "duplicate_article_id_examples": list(duplicate_catalog_ids)[:10],
            "missing_values_by_column": dict(sorted(missing_by_column.items())),
            "unique_article_ids": len(catalog_by_id),
            "unique_product_codes": len(product_code_types),
            "product_codes_with_multiple_group_or_type_assignments": sum(len(types) > 1 for types in product_code_types.values()),
            "multi_assignment_examples": [
                {"product_code": code, "assignments": sorted([list(value) for value in types])}
                for code, types in product_code_types.items() if len(types) > 1
            ][:20],
        },
        "weekly_aggregate_quality": {
            "rows": weekly_rows,
            "purchase_rows_represented": purchase_rows,
            "date_range_of_week_start": {"start": earliest_week, "end": latest_week},
            "duplicate_week_article_channel_keys": sum(count - 1 for count in weekly_key_counts.values() if count > 1),
            "matched_article_false_rows": matched_false_rows,
            "article_ids_missing_from_catalog": unmatched_rows,
            "malformed_or_negative_numeric_rows": malformed_numeric_rows + negative_units_rows,
            "price_mean_source_units_per_purchase_range": {"min": price_min, "max": price_max},
            "price_interpretation": "Source numeric scale only; currency and revenue meaning are not inferred.",
        },
        "category_reconciliation": {
            "style_rows_with_multi_assignment_category_conflict": style_category_conflicts,
            "categories_with_style_vs_article_level_difference": len(mismatches),
            "largest_differences": mismatches[:25],
            "method": "Article-level category totals join weekly article aggregates to article_catalog by article_id. Style summary uses representative attributes and is unsuitable as an exact category denominator when a product_code spans assignments.",
        },
        "interpretation_limits": [
            "Purchase rows are historical transaction rows, not unique customers, orders, or guaranteed unit demand.",
            "The source has no explicit merchandising season, current inventory, returns, stockouts, launch date, or customer cohort in this workflow.",
            "Price fields remain in source numeric units and are not interpreted as currency or revenue.",
            "sales_channel_id values are source codes without online/store labels.",
        ],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit processed H&M Kaggle data quality.")
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path; defaults to hm_quality_report.json in processed dir.")
    args = parser.parse_args(argv)
    report = audit(args.processed_dir)
    output = args.output or args.processed_dir / "hm_quality_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output.resolve()), "catalog_rows": report["catalog_quality"]["rows"], "weekly_rows": report["weekly_aggregate_quality"]["rows"], "purchase_rows": report["weekly_aggregate_quality"]["purchase_rows_represented"], "category_differences": report["category_reconciliation"]["categories_with_style_vs_article_level_difference"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
