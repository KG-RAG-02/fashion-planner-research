"""Local, provenance-preserving ingestion for the H&M Kaggle files.

This module only builds catalog and historical transaction aggregates. It does
not infer current stock, returns, discounts, demand, or commercial viability.
Customer identifiers are read only as source columns and never written to
derived outputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import sys
import time
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO


REQUIRED = {
    "articles.csv": {"article_id"},
    "transactions_train.csv": {
        "t_dat", "customer_id", "article_id", "price", "sales_channel_id"
    },
}
OUTPUT_ROOT = Path("data/processed/hm-personalized-fashion-recommendations")


class DataInputError(ValueError):
    """Raised when the downloaded source files are missing or unusable."""


@contextmanager
def _open_csv(source: Path, basename: str) -> Iterator[tuple[TextIO, str]]:
    """Open a CSV in a source directory or directly within a Kaggle ZIP."""
    if source.is_dir():
        matches = sorted(source.rglob(basename))
        if len(matches) > 1:
            raise DataInputError(f"Multiple {basename} files found under {source}")
        if matches:
            path = matches[0]
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                yield stream, str(path)
            return
        archives = []
        for archive_path in sorted(source.glob("*.zip")):
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    archives.extend(
                        (archive_path, name) for name in archive.namelist()
                        if Path(name).name == basename
                    )
            except zipfile.BadZipFile:
                continue
        if len(archives) != 1:
            raise DataInputError(
                f"Expected one {basename} in extracted files or one ZIP under {source}; found {len(archives)}"
            )
        archive_path, member = archives[0]
        with zipfile.ZipFile(archive_path) as archive:
            with archive.open(member, "r") as raw:
                import io

                with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                    yield stream, f"{archive_path}!/{member}"
        return

    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            matches = [name for name in archive.namelist() if Path(name).name == basename]
            if len(matches) != 1:
                raise DataInputError(
                    f"Expected one {basename} in {source}; found {len(matches)}"
                )
            with archive.open(matches[0], "r") as raw:
                import io

                with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                    yield stream, f"{source}!/{matches[0]}"
        return
    raise DataInputError(f"Source must be a directory or ZIP file: {source}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_pair(source: Path, members: tuple[str, str]) -> dict[str, dict[str, str | int]]:
    """Fingerprint both inputs while hashing a shared ZIP container only once."""
    resolved: dict[str, dict[str, str | int]] = {}
    shared_hash = _sha256(source) if source.is_file() else None
    for key, member in zip(("articles", "transactions"), members):
        if shared_hash is not None:
            resolved[key] = {
                "container": str(source),
                "container_sha256": shared_hash,
                "member": member.rsplit("!/", 1)[-1],
            }
        elif "!/" in member:
            archive, inner = member.split("!/", 1)
            archive_path = Path(archive)
            if str(archive_path) not in resolved:
                resolved[str(archive_path)] = {
                    "container": str(archive_path),
                    "container_sha256": _sha256(archive_path),
                }
            resolved[key] = {**resolved[str(archive_path)], "member": inner}
        else:
            path = Path(member)
            resolved[key] = {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
    return {key: value for key, value in resolved.items() if key in {"articles", "transactions"}}


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build(source: Path, output: Path = OUTPUT_ROOT, *, batch_size: int = 100_000) -> dict[str, object]:
    """Create an article catalog and weekly article/channel aggregates."""
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    interim = Path("data/interim").resolve()
    interim.mkdir(parents=True, exist_ok=True)
    db_path = interim / "hm_ingest_work.sqlite3"
    if db_path.exists():
        db_path.unlink()

    manifest: dict[str, object] = {
        "dataset": "H&M Personalized Fashion Recommendations",
        "pipeline_version": "0.1.0",
        "source": str(source),
        "source_ids": {"articles": "KAGGLE-HM-ARTICLES", "transactions": "KAGGLE-HM-TRANSACTIONS"},
        "source_metadata": {
            "dataset_url": "https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/data",
            "rules_url": "https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/rules",
            "license_status": "Subject to Kaggle competition rules; access is not an unrestricted open-data license.",
            "usage_boundary": "Follow competition rules for research/non-commercial use and team sharing. Do not redistribute raw files or send customer-level data to external providers.",
        },
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "outputs": {},
        "limitations": [
            "Historical purchase observations only; not a future-demand estimate.",
            "The Kaggle files do not establish current inventory, returns, stockouts, store IDs, or launch dates.",
            "Transaction price is retained as a source field; currency and discount baseline are not assumed.",
            "No basket/order identifier is available in the required transaction fields; co-purchase is not computed.",
            "Customer-level records and identifiers are not exported to derived files.",
        ],
    }
    article_ids: set[str] = set()
    article_rows = 0
    article_fields: list[str] = []
    article_source_name = ""
    blank_article_ids = 0
    duplicate_article_ids = 0

    try:
        with _open_csv(source, "articles.csv") as (stream, article_source_name):
            reader = csv.DictReader(stream)
            article_fields = reader.fieldnames or []
            missing = REQUIRED["articles.csv"] - set(article_fields)
            if missing:
                raise DataInputError(f"articles.csv missing columns: {sorted(missing)}")
            catalog_path = output / "article_catalog.csv"
            with catalog_path.open("w", encoding="utf-8", newline="") as dest:
                writer = csv.DictWriter(dest, fieldnames=article_fields, extrasaction="ignore")
                writer.writeheader()
                for row in reader:
                    article_id = (row.get("article_id") or "").strip()
                    if not article_id:
                        blank_article_ids += 1
                        continue
                    if article_id in article_ids:
                        duplicate_article_ids += 1
                        raise DataInputError(f"Duplicate article_id in articles.csv: {article_id}")
                    article_ids.add(article_id)
                    writer.writerow(row)
                    article_rows += 1

        manifest["article_catalog_rows"] = article_rows
        manifest["article_quality"] = {
            "blank_article_id_rows_excluded": blank_article_ids,
            "duplicate_article_id_rows": duplicate_article_ids,
        }
        manifest["article_columns"] = article_fields

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("CREATE TABLE tx (t_dat TEXT, article_id TEXT, channel TEXT, price REAL)")
            counters: Counter[str] = Counter()
            min_date: date | None = None
            max_date: date | None = None
            transaction_fields: list[str] = []
            transaction_source_name = ""
            batch: list[tuple[str, str, str, float]] = []
            started_at = time.monotonic()

            with _open_csv(source, "transactions_train.csv") as (stream, transaction_source_name):
                reader = csv.DictReader(stream)
                transaction_fields = reader.fieldnames or []
                missing = REQUIRED["transactions_train.csv"] - set(transaction_fields)
                if missing:
                    raise DataInputError(f"transactions_train.csv missing columns: {sorted(missing)}")
                for line_number, row in enumerate(reader, start=2):
                    counters["rows_read"] += 1
                    if counters["rows_read"] % 1_000_000 == 0:
                        elapsed = max(time.monotonic() - started_at, 0.001)
                        print(
                            f"Read {counters['rows_read']:,} transaction rows "
                            f"({counters['rows_read'] / elapsed:,.0f} rows/s)...",
                            file=sys.stderr,
                            flush=True,
                        )
                    try:
                        tx_date = _parse_date((row.get("t_dat") or "").strip())
                        article_id = (row.get("article_id") or "").strip()
                        channel = (row.get("sales_channel_id") or "").strip()
                        price = float((row.get("price") or "").strip())
                        if not article_id or not channel:
                            raise ValueError("blank article_id or sales_channel_id")
                        if not math.isfinite(price) or price < 0:
                            raise ValueError("price must be a finite non-negative number")
                    except (ValueError, TypeError):
                        counters["invalid_rows"] += 1
                        continue
                    counters["valid_rows"] += 1
                    if article_id not in article_ids:
                        counters["unknown_article_rows"] += 1
                    min_date = tx_date if min_date is None else min(min_date, tx_date)
                    max_date = tx_date if max_date is None else max(max_date, tx_date)
                    batch.append((tx_date.isoformat(), article_id, channel, price))
                    if len(batch) >= batch_size:
                        conn.executemany("INSERT INTO tx VALUES (?, ?, ?, ?)", batch)
                        conn.commit()
                        batch.clear()
            if batch:
                conn.executemany("INSERT INTO tx VALUES (?, ?, ?, ?)", batch)
                conn.commit()

            sales_path = output / "weekly_sales_by_article_channel.csv"
            aggregate_rows = 0
            with sales_path.open("w", encoding="utf-8", newline="") as dest:
                writer = csv.writer(dest)
                writer.writerow([
                    "week_start", "article_id", "sales_channel_id", "purchase_rows",
                    "price_sum_source_units", "price_mean_source_units", "price_min_source_units",
                    "price_max_source_units", "matched_article",
                ])
                query = """
                    SELECT date(t_dat, '-' || ((strftime('%w', t_dat) + 6) % 7) || ' days') AS week_start,
                           article_id, channel, COUNT(*), SUM(price), AVG(price), MIN(price), MAX(price)
                    FROM tx GROUP BY week_start, article_id, channel
                    ORDER BY week_start, article_id, channel
                """
                for week, article_id, channel, count, total, mean, minimum, maximum in conn.execute(query):
                    writer.writerow([
                        week, article_id, channel, count,
                        f"{total:.10g}", f"{mean:.10g}", f"{minimum:.10g}", f"{maximum:.10g}",
                        "true" if article_id in article_ids else "false",
                    ])
                    aggregate_rows += 1
            fingerprints = _fingerprint_pair(source, (article_source_name, transaction_source_name))
            manifest["source_articles"] = fingerprints["articles"]
            manifest["source_transactions"] = fingerprints["transactions"]
            manifest["transaction_columns"] = transaction_fields
            manifest["transaction_rows"] = dict(counters)
            manifest["observation_period"] = {
                "start": min_date.isoformat() if min_date else None,
                "end": max_date.isoformat() if max_date else None,
                "week_start": "Monday",
            }
            manifest["unmatched_article_rows"] = counters["unknown_article_rows"]
            manifest["outputs"] = {
                "article_catalog.csv": {"rows": article_rows, "sha256": _sha256(catalog_path)},
                "weekly_sales_by_article_channel.csv": {
                    "rows": aggregate_rows,
                    "sha256": _sha256(sales_path),
                },
            }
        finally:
            conn.close()
    finally:
        db_path.unlink(missing_ok=True)

    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from .hm_analysis import generate_analysis

    generate_analysis(output)
    from .hm_planning_analysis import write_outputs

    write_outputs(output)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process local H&M Kaggle product and transaction files.")
    parser.add_argument(
        "--source", type=Path,
        default=Path("data/raw/hm-personalized-fashion-recommendations"),
        help="Directory containing extracted Kaggle CSVs or the downloaded ZIP file.",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--batch-size", type=int, default=100_000, help="SQLite insert batch size (default: 100000).")
    args = parser.parse_args(argv)
    try:
        if args.batch_size < 1:
            raise DataInputError("--batch-size must be at least 1")
        manifest = build(args.source, args.output, batch_size=args.batch_size)
    except (DataInputError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"H&M data processing failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output),
        "observation_period": manifest["observation_period"],
        "transaction_rows": manifest["transaction_rows"],
        "limitations": manifest["limitations"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
