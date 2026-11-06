"""Aggregate actual H&M Kaggle outputs into provenance-tagged summaries.

Customer-level records are not read. All price measures keep their source
scale and are deliberately not labelled as currency or revenue.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/processed/hm-personalized-fashion-recommendations")
ANALYSIS_VERSION = "0.2.0"


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value or "0")
    except InvalidOperation as exc:
        raise ValueError(f"Invalid source price aggregate: {value!r}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def generate_analysis(processed_dir: Path = DEFAULT_DIR) -> dict[str, Any]:
    """Create product, category, channel, color, and monthly summaries."""
    processed_dir = processed_dir.expanduser().resolve()
    manifest_path = processed_dir / "manifest.json"
    catalog_path = processed_dir / "article_catalog.csv"
    weekly_path = processed_dir / "weekly_sales_by_article_channel.csv"
    for required in (manifest_path, catalog_path, weekly_path):
        if not required.is_file():
            raise FileNotFoundError(f"Required processed input is missing: {required}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    observation = manifest.get("observation_period", {})

    articles: dict[str, dict[str, str]] = {}
    styles: dict[str, dict[str, Any]] = {}
    with catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required_columns = {"article_id", "product_code", "prod_name", "product_group_name", "product_type_name", "colour_group_name"}
        missing = required_columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"article_catalog.csv missing columns: {sorted(missing)}")
        for row in reader:
            article_id = row["article_id"].strip()
            if not article_id:
                continue
            metadata = {
                "product_code": row["product_code"].strip(),
                "prod_name": row["prod_name"].strip(),
                "product_group_name": row["product_group_name"].strip() or "(unknown)",
                "product_type_name": row["product_type_name"].strip() or "(unknown)",
                "colour_group_name": row["colour_group_name"].strip() or "(unknown)",
                "department_name": row.get("department_name", "").strip(),
                "section_name": row.get("section_name", "").strip(),
                "index_name": row.get("index_name", "").strip(),
                "detail_desc": row.get("detail_desc", "").strip(),
            }
            articles[article_id] = metadata
            style_id = metadata["product_code"] or "(unknown)"
            if style_id not in styles:
                styles[style_id] = {
                    **metadata,
                    "style_id": style_id,
                    "article_ids": set(),
                    "units": 0,
                    "price_sum": Decimal("0"),
                    "channels": Counter(),
                }
            styles[style_id]["article_ids"].add(article_id)

    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    colors: dict[tuple[str, str, str], dict[str, Any]] = {}
    monthly: dict[tuple[str, str, str], dict[str, Any]] = {}
    channels: dict[str, dict[str, Any]] = {}
    article_totals: dict[str, dict[str, Any]] = {}
    units_total = 0
    price_sum_total = Decimal("0")
    aggregate_rows = 0
    unmatched_article_rows = 0

    with weekly_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required_columns = {"week_start", "article_id", "sales_channel_id", "purchase_rows", "price_sum_source_units"}
        missing = required_columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"weekly_sales_by_article_channel.csv missing columns: {sorted(missing)}")
        for row in reader:
            aggregate_rows += 1
            article_id = row["article_id"]
            channel = row["sales_channel_id"]
            units = int(row["purchase_rows"])
            price_sum = _decimal(row["price_sum_source_units"])
            meta = articles.get(article_id)
            if meta is None:
                unmatched_article_rows += 1
                meta = {
                    "product_code": "(unknown)", "prod_name": "(unknown)",
                    "product_group_name": "(unknown)", "product_type_name": "(unknown)",
                    "colour_group_name": "(unknown)", "department_name": "",
                    "section_name": "", "index_name": "", "detail_desc": "",
                }
            style_id = meta["product_code"] or "(unknown)"
            week = row["week_start"]
            month = week[:7]  # The month is the month containing the Monday week start.
            units_total += units
            price_sum_total += price_sum

            article = article_totals.setdefault(article_id, {
                **meta, "article_id": article_id, "units": 0,
                "price_sum": Decimal("0"), "channels": Counter(),
            })
            article["units"] += units
            article["price_sum"] += price_sum
            article["channels"][channel] += units

            style = styles.setdefault(style_id, {
                **meta, "style_id": style_id, "article_ids": set(),
                "units": 0, "price_sum": Decimal("0"), "channels": Counter(),
            })
            style["article_ids"].add(article_id)
            style["units"] += units
            style["price_sum"] += price_sum
            style["channels"][channel] += units

            group_key = (meta["product_group_name"], meta["product_type_name"], channel)
            group = groups.setdefault(group_key, {"units": 0, "price_sum": Decimal("0"), "articles": set(), "styles": set()})
            group["units"] += units
            group["price_sum"] += price_sum
            group["articles"].add(article_id)
            group["styles"].add(style_id)

            color_key = (meta["colour_group_name"], meta["product_group_name"], channel)
            color = colors.setdefault(color_key, {"units": 0, "price_sum": Decimal("0"), "articles": set()})
            color["units"] += units
            color["price_sum"] += price_sum
            color["articles"].add(article_id)

            month_key = (month, meta["product_group_name"], channel)
            month_group = monthly.setdefault(month_key, {"units": 0, "price_sum": Decimal("0"), "articles": set()})
            month_group["units"] += units
            month_group["price_sum"] += price_sum
            month_group["articles"].add(article_id)

            channel_group = channels.setdefault(channel, {"units": 0, "price_sum": Decimal("0"), "articles": set(), "styles": set()})
            channel_group["units"] += units
            channel_group["price_sum"] += price_sum
            channel_group["articles"].add(article_id)
            channel_group["styles"].add(style_id)

    if units_total == 0:
        raise ValueError("No valid purchase rows were found in the processed weekly aggregate.")

    source_period = f"{observation.get('start')}–{observation.get('end')}"
    sales_source_id = "KAGGLE-HM-TRANSACTIONS"
    article_source_id = "KAGGLE-HM-ARTICLES"
    sales_fingerprint = str(manifest.get("source_transactions", {}).get("container_sha256", "unknown"))[:12]
    catalog_fingerprint = str(manifest.get("source_articles", {}).get("container_sha256", "unknown"))[:12]

    category_rows = []
    for (product_group, product_type, channel), metric in groups.items():
        category_rows.append({
            "product_group_name": product_group, "product_type_name": product_type,
            "sales_channel_id": channel, "purchase_rows": metric["units"],
            "price_sum_source_units": format(metric["price_sum"], "f"),
            "price_mean_source_units": format(metric["price_sum"] / metric["units"], ".12g"),
            "unique_article_count": len(metric["articles"]), "unique_product_code_count": len(metric["styles"]),
            "observation_period": source_period, "source_id": sales_source_id,
        })
    category_rows.sort(key=lambda item: item["purchase_rows"], reverse=True)

    color_rows = []
    for (color_name, product_group, channel), metric in colors.items():
        color_rows.append({
            "colour_group_name": color_name, "product_group_name": product_group,
            "sales_channel_id": channel, "purchase_rows": metric["units"],
            "price_mean_source_units": format(metric["price_sum"] / metric["units"], ".12g"),
            "unique_article_count": len(metric["articles"]),
            "observation_period": source_period, "source_id": sales_source_id,
        })
    color_rows.sort(key=lambda item: item["purchase_rows"], reverse=True)

    monthly_rows = []
    for (month, product_group, channel), metric in sorted(monthly.items()):
        monthly_rows.append({
            "month_by_week_start": month, "product_group_name": product_group,
            "sales_channel_id": channel, "purchase_rows": metric["units"],
            "price_mean_source_units": format(metric["price_sum"] / metric["units"], ".12g"),
            "unique_article_count": len(metric["articles"]), "source_id": sales_source_id,
        })

    style_rows = []
    for style in styles.values():
        if not style["units"]:
            continue
        top_channel = style["channels"].most_common(1)[0][0] if style["channels"] else ""
        sales_evidence_id = f"E-HM-SALES-{sales_fingerprint}-{style['style_id']}-{observation.get('start')}-{observation.get('end')}"
        catalog_evidence_id = f"E-HM-CATALOG-{catalog_fingerprint}-{style['style_id']}"
        style_rows.append({
            "product_code": style["style_id"], "representative_article_id": min(style["article_ids"]) if style["article_ids"] else "",
            "representative_prod_name": style["prod_name"], "product_group_name": style["product_group_name"],
            "product_type_name": style["product_type_name"], "representative_colour_group_name": style["colour_group_name"],
            "department_name": style["department_name"], "section_name": style["section_name"], "index_name": style["index_name"],
            "article_id_count": len(style["article_ids"]), "purchase_rows": style["units"],
            "price_sum_source_units": format(style["price_sum"], "f"),
            "price_mean_source_units": format(style["price_sum"] / style["units"], ".12g"),
            "top_sales_channel_id": top_channel, "observation_period": source_period,
            "catalog_source_id": article_source_id, "sales_source_id": sales_source_id,
            "sales_evidence_id": sales_evidence_id, "catalog_evidence_id": catalog_evidence_id,
        })
    style_rows.sort(key=lambda item: item["purchase_rows"], reverse=True)

    article_rows = []
    for article in article_totals.values():
        top_channel = article["channels"].most_common(1)[0][0] if article["channels"] else ""
        article_rows.append({
            "article_id": article["article_id"], "product_code": article["product_code"],
            "prod_name": article["prod_name"], "product_group_name": article["product_group_name"],
            "product_type_name": article["product_type_name"], "colour_group_name": article["colour_group_name"],
            "purchase_rows": article["units"], "price_mean_source_units": format(article["price_sum"] / article["units"], ".12g"),
            "top_sales_channel_id": top_channel, "observation_period": source_period,
            "catalog_source_id": article_source_id, "sales_source_id": sales_source_id,
        })
    article_rows.sort(key=lambda item: item["purchase_rows"], reverse=True)

    channel_rows = []
    for channel, metric in channels.items():
        channel_rows.append({
            "sales_channel_id": channel, "purchase_rows": metric["units"],
            "price_mean_source_units": format(metric["price_sum"] / metric["units"], ".12g"),
            "unique_article_count": len(metric["articles"]), "unique_product_code_count": len(metric["styles"]),
            "observation_period": source_period, "source_id": sales_source_id,
        })
    channel_rows.sort(key=lambda item: item["sales_channel_id"])

    prefix = "hm_"
    files = {
        "style_sales_summary.csv": (style_rows, ["product_code", "representative_article_id", "representative_prod_name", "product_group_name", "product_type_name", "representative_colour_group_name", "department_name", "section_name", "index_name", "article_id_count", "purchase_rows", "price_sum_source_units", "price_mean_source_units", "top_sales_channel_id", "observation_period", "catalog_source_id", "sales_source_id", "sales_evidence_id", "catalog_evidence_id"]),
        "top_article_sales.csv": (article_rows[:100], ["article_id", "product_code", "prod_name", "product_group_name", "product_type_name", "colour_group_name", "purchase_rows", "price_mean_source_units", "top_sales_channel_id", "observation_period", "catalog_source_id", "sales_source_id"]),
        "category_sales_summary.csv": (category_rows, ["product_group_name", "product_type_name", "sales_channel_id", "purchase_rows", "price_sum_source_units", "price_mean_source_units", "unique_article_count", "unique_product_code_count", "observation_period", "source_id"]),
        "color_sales_summary.csv": (color_rows, ["colour_group_name", "product_group_name", "sales_channel_id", "purchase_rows", "price_mean_source_units", "unique_article_count", "observation_period", "source_id"]),
        "monthly_product_group_sales.csv": (monthly_rows, ["month_by_week_start", "product_group_name", "sales_channel_id", "purchase_rows", "price_mean_source_units", "unique_article_count", "source_id"]),
        "channel_sales_summary.csv": (channel_rows, ["sales_channel_id", "purchase_rows", "price_mean_source_units", "unique_article_count", "unique_product_code_count", "observation_period", "source_id"]),
    }
    output_manifest: dict[str, dict[str, Any]] = {}
    for filename, (rows, columns) in files.items():
        target = processed_dir / filename
        _write_csv(target, columns, rows)
        output_manifest[filename] = {"rows": len(rows), "sha256": _sha256(target)}

    total_price_mean = price_sum_total / units_total
    group_total: Counter[str] = Counter()
    for (group_name, _type_name, _channel), metric in groups.items():
        group_total[group_name] += metric["units"]
    summary = {
        "analysis_version": ANALYSIS_VERSION,
        "dataset": "H&M Personalized Fashion Recommendations Kaggle competition data",
        "data_status": "historical_public_competition_dataset; not live internal H&M data",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_manifest": "manifest.json",
        "source_ids": {"articles": article_source_id, "transactions": sales_source_id},
        "source_provenance": {
            "metadata": manifest.get("source_metadata", {}),
            "articles": manifest.get("source_articles", {}),
            "transactions": manifest.get("source_transactions", {}),
        },
        "observation_period": observation,
        "article_catalog_count": len(articles),
        "product_code_count": len(styles),
        "weekly_article_channel_aggregate_rows": aggregate_rows,
        "purchase_rows": units_total,
        "unmatched_article_aggregate_rows": unmatched_article_rows,
        "sales_channel_ids": [item["sales_channel_id"] for item in channel_rows],
        "channels": channel_rows,
        "top_product_groups": [
            {"product_group_name": name, "purchase_rows": count, "share_of_purchase_rows": round(count / units_total, 6)}
            for name, count in group_total.most_common(10)
        ],
        "top_styles": style_rows[:10],
        "top_articles": article_rows[:10],
        "top_colors": color_rows[:10],
        "normalized_price": {
            "mean_source_units_per_purchase": format(total_price_mean, ".12g"),
            "interpretation": "Kaggle source price scale only; currency, list price, discount, and revenue are not inferred.",
        },
        "source_columns": {
            "article_attributes": ["product_code", "product_group_name", "product_type_name", "colour_group_name", "department_name", "section_name", "index_name"],
            "transaction_attributes": ["t_dat", "article_id", "price", "sales_channel_id"],
            "customer_id": "present in source but excluded from processing outputs and analysis",
        },
        "limitations": [
            "This is historical Kaggle competition data, not a current internal H&M operational feed.",
            "The source does not provide current inventory, store identifiers, returns, stockouts, launch dates, reviews, or inquiries.",
            "The source has no explicit season field; the sales period is not assigned to a merchandising season.",
            "The source price is a normalized numeric value; it is not presented as KRW, revenue, or a discount rate.",
            "Channel IDs remain source codes 1 and 2; no online/store interpretation is added.",
            "No customer-level analysis or co-purchase analysis is performed; no basket/order identifier is available here.",
            "Monthly summaries bucket each Monday-starting week by its week_start month.",
            "Trend documents and customer feedback are not part of this dataset; opportunity claims remain limited to product and purchase observations.",
            "Use and sharing remain subject to the Kaggle competition rules recorded in the data source plan.",
        ],
        "outputs": output_manifest,
    }
    report_path = processed_dir / "hm_analysis.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_manifest[report_path.name] = {"rows": 1, "sha256": _sha256(report_path)}

    manifest["analysis"] = {
        "analysis_version": ANALYSIS_VERSION,
        "purchase_rows": units_total,
        "product_code_count": len(styles),
        "outputs": output_manifest,
        "limitations": summary["limitations"],
    }
    manifest.setdefault("outputs", {}).update(output_manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze locally processed H&M Kaggle aggregates.")
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    try:
        summary = generate_analysis(args.processed_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"H&M analysis failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "observation_period": summary["observation_period"],
        "purchase_rows": summary["purchase_rows"],
        "product_code_count": summary["product_code_count"],
        "top_product_groups": summary["top_product_groups"][:5],
        "channels": summary["channels"],
        "limitations": summary["limitations"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
