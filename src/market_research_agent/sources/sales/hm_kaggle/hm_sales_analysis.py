"""Article-level H&M historical sales analysis.

The module derives category, seasonality, color, source-price, and channel
summaries from local processed data. It does not propose products, infer stock,
returns, customer preference, or future demand.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/processed/hm-personalized-fashion-recommendations")
VERSION = "1.0.0"
SOURCE_SALES = "KAGGLE-HM-TRANSACTIONS"
SOURCE_CATALOG = "KAGGLE-HM-ARTICLES"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def analyze(processed_dir: Path = DEFAULT_DIR) -> dict[str, Any]:
    root = processed_dir.expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    sales_fingerprint = str(manifest.get("source_transactions", {}).get("container_sha256", "unknown"))[:12]
    catalog_fingerprint = str(manifest.get("source_articles", {}).get("container_sha256", "unknown"))[:12]
    catalog_rows = _read(root / "article_catalog.csv")
    weekly_rows = _read(root / "weekly_sales_by_article_channel.csv")
    catalog = {row["article_id"].strip(): row for row in catalog_rows if row.get("article_id", "").strip()}
    articles: set[str] = set()
    styles: set[str] = set()
    category: dict[tuple[str, str], dict[str, Any]] = {}
    group: dict[str, int] = Counter()
    color: dict[tuple[str, str, str], int] = Counter()
    channel: dict[tuple[str, str, str], int] = Counter()
    week_type: dict[tuple[str, str, str], int] = Counter()
    month_type: dict[tuple[str, str, str], int] = Counter()
    year_type: dict[tuple[str, str, int], int] = Counter()
    year_month_type: dict[tuple[str, str, int, int], int] = Counter()
    type_price_rows: dict[tuple[str, str], list[tuple[float, int]]] = defaultdict(list)
    style_category_sales: dict[tuple[str, str, str], int] = Counter()
    total = 0
    joined_rows = 0
    unmatched = 0
    source_min: float | None = None
    source_max: float | None = None

    for row in weekly_rows:
        article_id = row["article_id"].strip()
        meta = catalog.get(article_id)
        if meta is None:
            unmatched += 1
            continue
        units = int(row["purchase_rows"])
        if units < 0:
            raise ValueError(f"Negative purchase_rows for article {article_id}")
        week = datetime.strptime(row["week_start"], "%Y-%m-%d").date()
        group_name = meta.get("product_group_name", "").strip() or "(unknown)"
        type_name = meta.get("product_type_name", "").strip() or "(unknown)"
        color_name = meta.get("colour_group_name", "").strip() or "(unknown)"
        product_code = meta.get("product_code", "").strip() or "(unknown)"
        key = (group_name, type_name)
        price_mean = float(row.get("price_mean_source_units", "0") or "0")
        source_min = price_mean if source_min is None else min(source_min, price_mean)
        source_max = price_mean if source_max is None else max(source_max, price_mean)
        total += units
        joined_rows += 1
        articles.add(article_id)
        styles.add(product_code)
        metric = category.setdefault(key, {"units": 0, "articles": set(), "styles": set(), "price_weighted_sum": 0.0})
        metric["units"] += units
        metric["articles"].add(article_id)
        metric["styles"].add(product_code)
        metric["price_weighted_sum"] += price_mean * units
        group[group_name] += units
        color[(group_name, type_name, color_name)] += units
        channel[(group_name, type_name, row["sales_channel_id"])] += units
        week_type[(group_name, type_name, week.isoformat())] += units
        month_type[(group_name, type_name, week.strftime("%m"))] += units
        year_type[(group_name, type_name, week.year)] += units
        year_month_type[(group_name, type_name, week.year, week.month)] += units
        type_price_rows[(group_name, type_name)].append((price_mean, units))
        style_category_sales[(group_name, type_name, product_code)] += units

    if not total:
        raise ValueError("No joined purchase rows available for analysis")

    type_rows: list[dict[str, Any]] = []
    group_rows = []
    for name, units in group.most_common():
        group_rows.append({"product_group_name": name, "purchase_rows": units, "share": _safe_rate(units, total)})
    category_denominator = sum(metric["units"] for metric in category.values())
    for key, metric in sorted(category.items(), key=lambda item: item[1]["units"], reverse=True):
        units = metric["units"]
        category_token = hashlib.sha256(f"{key[0]}\0{key[1]}".encode("utf-8")).hexdigest()[:12]
        type_rows.append({
            "product_group_name": key[0], "product_type_name": key[1],
            "purchase_rows": units, "share_of_joined_purchase_rows": _safe_rate(units, total),
            "share_of_category_mapped_rows": _safe_rate(units, category_denominator),
            "unique_article_count": len(metric["articles"]), "unique_product_code_count": len(metric["styles"]),
            "weighted_mean_price_source_units": round(metric["price_weighted_sum"] / units, 10) if units else None,
            "source_ids": [SOURCE_SALES, SOURCE_CATALOG],
            "evidence_id": f"E-HM-CAT-{sales_fingerprint}-{catalog_fingerprint}-{category_token}",
        })
    running = 0
    for row in type_rows:
        running += row["purchase_rows"]
        row["cumulative_share_of_category_mapped_rows"] = _safe_rate(running, category_denominator)

    type_keys = set(category)
    seasonality = []
    for key in sorted(type_keys):
        annual = {year: units for (g, t, year), units in year_type.items() if (g, t) == key}
        monthly = Counter()
        for (g, t, _year, month), units in year_month_type.items():
            if (g, t) == key:
                monthly[month] += units
        active_months = sorted(monthly, key=lambda month: (-monthly[month], month))
        first_year, last_year = min(annual), max(annual)
        first_units, last_units = annual[first_year], annual[last_year]
        total_type_units = category[key]["units"]
        comparable = (last_year - first_year == 1 and first_units >= 100 and last_units >= 100 and total_type_units >= 1000)
        seasonality.append({
            "product_group_name": key[0], "product_type_name": key[1],
            "monthly_peak_week_start_months": [int(x) for x in active_months[:3]],
            "annual_purchase_rows": [{"year": year, "purchase_rows": annual[year]} for year in sorted(annual)],
            "first_to_last_observed_year_growth": round(last_units / first_units - 1, 6) if comparable else None,
            "growth_comparable": comparable,
            "growth_note": "Calendar-year totals are not exposure-normalized; edge years are incomplete and growth is descriptive only.",
            "weekly_series": [
                {"week_start": week, "purchase_rows": units}
                for (g, t, week), units in sorted(week_type.items()) if (g, t) == key
            ],
        })

    color_rows = []
    color_totals: Counter[tuple[str, str]] = Counter()
    for (g, t, c), units in color.items():
        color_totals[(g, t)] += units
    for (g, t, c), units in sorted(color.items(), key=lambda item: item[1], reverse=True):
        color_rows.append({"product_group_name": g, "product_type_name": t, "colour_group_name": c,
                           "purchase_rows": units, "share_within_type": _safe_rate(units, color_totals[(g, t)]),
                           "minimum_recommended_sample": 100, "sample_sufficient": units >= 100,
                           "source_ids": [SOURCE_SALES, SOURCE_CATALOG]})
    channel_rows = []
    channel_totals: Counter[tuple[str, str]] = Counter()
    for (g, t, ch), units in channel.items():
        channel_totals[(g, t)] += units
    for (g, t, ch), units in sorted(channel.items(), key=lambda item: item[1], reverse=True):
        channel_rows.append({"product_group_name": g, "product_type_name": t, "sales_channel_id": ch,
                             "purchase_rows": units, "share_within_type": _safe_rate(units, channel_totals[(g, t)]),
                             "channel_label": "unmapped source code", "source_ids": [SOURCE_SALES]})

    price_rows = []
    for key, values in type_price_rows.items():
        observed_values = [price for price, _units in values]
        ordered = sorted(observed_values)
        thresholds = [ordered[min(len(ordered) - 1, math.ceil(len(ordered) * q / 5) - 1)] for q in range(1, 5)] if len(ordered) >= 5 else []
        # Relative quintile bands describe the price field's source scale only.
        bands = Counter()
        band_price_sum = Counter()
        for price, units in values:
            band = 1 + sum(price > threshold for threshold in thresholds)
            bands[band] += units
            band_price_sum[band] += price * units
        for band, units in sorted(bands.items()):
            price_rows.append({"product_group_name": key[0], "product_type_name": key[1],
                               "relative_source_price_quintile": band if thresholds else None,
                               "purchase_rows": units, "share_within_type": _safe_rate(units, category[key]["units"]),
                               "weighted_mean_source_price_in_band": round(band_price_sum[band] / units, 10) if units else None,
                               "unit_note": "Source numeric scale only; not currency, revenue, list price, or discount.",
                               "threshold_method": "Article-week-channel mean-price quintiles (article-weeks equally weighted to set cut points); purchase rows weighted within band."})

    concentration = []
    for key, metric in category.items():
        style_sales = Counter({style: units for (g, t, style), units in style_category_sales.items() if (g, t) == key})
        top5 = sum(units for _, units in style_sales.most_common(5))
        concentration.append({"product_group_name": key[0], "product_type_name": key[1],
                              "purchase_rows": metric["units"], "style_count": len(style_sales),
                              "top_1_style_share": _safe_rate(style_sales.most_common(1)[0][1], metric["units"]) if style_sales else None,
                              "top_5_style_share": _safe_rate(top5, metric["units"]),
                              "interpretation": "Historical purchase concentration by product_code; not evidence of future launch success."})

    top_group = sorted(group_rows, key=lambda row: row["purchase_rows"], reverse=True)
    result = {
        "analysis_version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "dataset": manifest.get("dataset"), "observation_period": manifest.get("observation_period"),
        "grain": "weekly article_id x sales_channel_id aggregate joined to article_catalog by article_id",
        "purchase_rows": total, "joined_weekly_rows": joined_rows, "unmatched_weekly_rows": unmatched,
        "unique_article_ids_sold": len(articles), "unique_product_codes_sold": len(styles),
        "source_price_observed_range": {"minimum_article_week_channel_mean": source_min, "maximum_article_week_channel_mean": source_max,
                                         "interpretation": "Source price scale; currency and commercial meaning are unverified."},
        "methodology": {
            "category_denominator": "Article-level joins; multi-category styles retain each article's own catalog assignment.",
            "monthly_seasonality": "Calendar month of Monday week_start; displayed as observed month pattern, not merchandising season.",
            "growth": "First vs last available calendar year, only when consecutive and nonzero; not exposure normalized; descriptive only.",
            "price_bands": "Within each type, quintiles of article-week-channel average source price; no monetary label.",
            "concentration": "Share of type purchases attributed to top product_code styles.",
            "minimum_color_sample": 100,
        },
        "product_groups": group_rows, "product_types": type_rows,
        "seasonality_and_growth": seasonality, "color_mix": color_rows,
        "relative_source_price_bands": price_rows, "channel_mix": channel_rows,
        "style_concentration": concentration,
        "limitations": [
            "Historical Kaggle public competition dataset, not current internal H&M data.",
            "Purchase rows are not unique orders, customers, or future demand.",
            "No explicit merchandising season, inventory, returns, stockouts, launch date, customer attributes, or market trend data.",
            "Price remains unverified source numeric scale; channel labels remain unmapped source codes.",
        ],
    }
    return result


def write_outputs(processed_dir: Path = DEFAULT_DIR) -> dict[str, Any]:
    root = processed_dir.expanduser().resolve()
    result = analyze(root)
    path = root / "hm_sales_analysis.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("sales_analysis", {}).update({"version": VERSION, "output": path.name, "sha256": _sha256(path), "purchase_rows": result["purchase_rows"]})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate article-level H&M historical sales analysis.")
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    try:
        result = write_outputs(args.processed_dir)
    except (OSError, ValueError, json.JSONDecodeError, KeyError):
        print("H&M historical sales analysis failed.", file=sys.stderr)
        return 2
    print(json.dumps({"output": str((args.processed_dir / "hm_sales_analysis.json").resolve()),
                      "purchase_rows": result["purchase_rows"], "product_types": len(result["product_types"]),
                      "joined_weekly_rows": result["joined_weekly_rows"], "unmatched_weekly_rows": result["unmatched_weekly_rows"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
