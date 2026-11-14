"""Build and read one versioned R2 snapshot for all product-review sources.

H&M customer and transaction rows become suppressed category aggregates. The
bounded owner-provided fabric listing CSV is bundled so a review can fetch all
source material with one cold-process R2 GET and run no request-time DuckDB.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...market.fabric_price_r2_source import DEFAULT_FABRIC_PRICE_KEY, R2FabricPriceSource
from ....sources.shared.r2_object_store import R2ObjectError, R2ObjectStore, R2Settings
from ....sources.shared.r2_source_manifest import r2_source_key
from .duckdb_http import configure_httpfs
from .hm_customer_age_source import (
    _CSV_COLUMNS, _MAPPING_PATH, _build_profiles, _checked_key, _mapped_requested_category,
)
from .hm_price_source import _sql_string


SCHEMA_VERSION = "hm-sales-market-aggregate/1.1"
_MAX_SOURCE_BYTES = {
    "hm_customers": 300_000_000,
    "hm_articles": 50_000_000,
    "hm_transactions": 1_000_000_000,
}
_MAX_SNAPSHOT_BYTES = 16_000_000
_MAX_FABRIC_BYTES = 1_000_000
_OUTPUT_PREFIX = "derived/research-aggregate-snapshots"
_LOGGER = logging.getLogger(__name__)
_CACHE: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
_INFLIGHT: dict[tuple[str, ...], Future[dict[str, Any]]] = {}
_LOCK = threading.Lock()
_CACHE_MAX_SNAPSHOTS = 4


def _mapping_digest(mapping: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _source_keys() -> tuple[str, str, str]:
    return tuple(
        _checked_key(r2_source_key(name, default=default), default)
        for name, default in (
            ("hm_customers", "customers.csv"),
            ("hm_articles", "articles.parquet"),
            ("hm_transactions", "transactions_train.parquet"),
        )
    )


def _object_version(metadata: dict[str, Any]) -> str:
    return str(metadata.get("VersionId") or metadata.get("ETag", "")).strip('"')


def _aggregate_sql(settings: R2Settings, keys: tuple[str, str, str]) -> str:
    customers_uri = _sql_string(f"s3://{settings.bucket}/{keys[0]}")
    articles_uri = _sql_string(f"s3://{settings.bucket}/{keys[1]}")
    transactions_uri = _sql_string(f"s3://{settings.bucket}/{keys[2]}")
    customer_schema = "{" + _CSV_COLUMNS + "}"
    return f"""
        WITH mapped_articles AS (
            SELECT a.article_id, a.index_group_name,
                   COALESCE(type_map.mapped_category, group_map.mapped_category) AS category
            FROM read_parquet({articles_uri}) AS a
            LEFT JOIN hm_category_map AS type_map
              ON type_map.dimension = 'type'
             AND lower(trim(a.product_type_name)) = lower(trim(type_map.source_value))
            LEFT JOIN hm_category_map AS group_map
              ON group_map.dimension = 'group'
             AND lower(trim(a.product_group_name)) = lower(trim(group_map.source_value))
        ), customers_raw AS (
            SELECT customer_id, age FROM read_csv(
                {customers_uri}, header=true, delim=',', columns={customer_schema}, sample_size=1000
            )
        ), customers AS (
            SELECT customer_id,
                   CASE WHEN COUNT(DISTINCT age) = 1 THEN MAX(age) ELSE NULL END AS age
            FROM customers_raw
            GROUP BY customer_id
        ), joined AS (
            SELECT a.category, a.index_group_name, t.customer_id, c.customer_id AS matched_customer_id,
                   c.age, t.article_id, TRY_CAST(t.t_dat AS DATE) AS transaction_date,
                   t.price, t.sales_channel_id,
                   CASE WHEN c.customer_id IS NULL THEN NULL
                        WHEN c.age BETWEEN 16 AND 100 THEN
                          CASE WHEN c.age < 20 THEN 'under_20' WHEN c.age < 30 THEN '20_29'
                               WHEN c.age < 40 THEN '30_39' WHEN c.age < 50 THEN '40_49'
                               ELSE '50_plus' END
                        ELSE 'unknown_age' END AS age_band
            FROM read_parquet({transactions_uri}) AS t
            INNER JOIN mapped_articles AS a USING (article_id)
            LEFT JOIN customers AS c USING (customer_id)
            WHERE a.category IS NOT NULL
        )
        SELECT category,
               CASE WHEN GROUPING(index_group_name) = 1 THEN '__all__'
                    ELSE COALESCE(index_group_name, '__unknown__') END AS merchandise_segment,
               CASE WHEN GROUPING(age_band) = 1 THEN 'price' ELSE 'age' END AS measure,
               CASE WHEN GROUPING(age_band) = 1 THEN NULL ELSE age_band END AS age_band,
               COUNT(DISTINCT matched_customer_id)::BIGINT AS unique_customers,
               CASE WHEN GROUPING(age_band) = 1 THEN
                    MIN(transaction_date) FILTER (WHERE price IS NOT NULL)
                    ELSE MIN(transaction_date) FILTER (WHERE matched_customer_id IS NOT NULL) END::VARCHAR AS period_start,
               CASE WHEN GROUPING(age_band) = 1 THEN
                    MAX(transaction_date) FILTER (WHERE price IS NOT NULL)
                    ELSE MAX(transaction_date) FILTER (WHERE matched_customer_id IS NOT NULL) END::VARCHAR AS period_end,
               COUNT(*) FILTER (WHERE price IS NOT NULL)::BIGINT AS transaction_rows,
               COUNT(DISTINCT article_id) FILTER (WHERE price IS NOT NULL)::BIGINT AS article_count,
               MIN(price), quantile_cont(price, 0.25), quantile_cont(price, 0.5),
               quantile_cont(price, 0.75), MAX(price), AVG(price),
               list(DISTINCT CAST(sales_channel_id AS VARCHAR)) FILTER (WHERE price IS NOT NULL) AS channel_codes
        FROM joined
        GROUP BY GROUPING SETS (
            (category, index_group_name, age_band),
            (category, age_band),
            (category, index_group_name),
            (category)
        )
    """


def _run_aggregate_query(
    duckdb: Any, settings: R2Settings, keys: tuple[str, str, str], mapping: dict[str, Any],
) -> list[tuple[Any, ...]]:
    connection = duckdb.connect()
    try:
        extension_dir = os.getenv("VOGUE_DUCKDB_EXTENSION_DIRECTORY", "").strip()
        if extension_dir:
            connection.execute("SET extension_directory = " + _sql_string(extension_dir))
        connection.execute("LOAD httpfs")
        configure_httpfs(connection)
        endpoint = urlsplit(settings.endpoint).netloc if settings.endpoint else f"{settings.account_id}.r2.cloudflarestorage.com"
        connection.execute(
            "CREATE SECRET hm_aggregate_r2 (TYPE S3, KEY_ID " + _sql_string(settings.access_key_id)
            + ", SECRET " + _sql_string(settings.secret_access_key) + ", ENDPOINT " + _sql_string(endpoint)
            + ", REGION 'auto', URL_STYLE 'path', USE_SSL true)"
        )
        connection.execute("CREATE TEMP TABLE hm_category_map (source_value VARCHAR, dimension VARCHAR, mapped_category VARCHAR)")
        rows = [(key, "type", value) for key, value in mapping["product_type_name"].items()]
        rows.extend((key, "group", value) for key, value in mapping["product_group_name"].items())
        connection.executemany("INSERT INTO hm_category_map VALUES (?, ?, ?)", rows)
        return connection.execute(_aggregate_sql(settings, keys)).fetchall()
    finally:
        connection.close()


def _price_record(row: tuple[Any, ...], *, source_refs: tuple[dict[str, str], ...],
                  mapping: dict[str, Any], mapping_fingerprint: str) -> dict[str, Any]:
    category, segment = str(row[0]), str(row[1])
    channels = sorted(str(item) for item in (row[15] or []))
    statistics = [None if item is None else float(item) for item in row[9:15]]
    return {
        "category": category,
        "merchandise_segment": segment,
        "sample_count": int(row[7]),
        "article_count": int(row[8]),
        "period": {"start": str(row[5]), "end": str(row[6])},
        "channel_codes": channels,
        "price_statistics": {
            "minimum": statistics[0], "p25": statistics[1], "median": statistics[2],
            "p75": statistics[3], "maximum": statistics[4], "mean": statistics[5],
            "currency": None, "unit": "normalized_source_price; currency not provided",
            "statistic": "transaction_price_distribution; unweighted transaction rows",
        },
        "sources": list(source_refs),
        "category_mapping": {
            "mapping_id": mapping["mapping_id"], "mapping_version": mapping["mapping_version"],
            "mapping_fingerprint": mapping_fingerprint,
            "matching": "product_type_exact_then_product_group_fallback",
        },
    }


def build_snapshot_payload(
    rows: list[tuple[Any, ...]], *, source_refs: tuple[dict[str, str], ...],
    mapping: dict[str, Any], mapping_fingerprint: str,
    fabric_source_ref: dict[str, str] | None = None,
    fabric_listing_csv: str | None = None,
) -> dict[str, Any]:
    """Convert one combined DuckDB result into suppressed, category-level JSON."""
    age_rows = [
        (str(row[0]), str(row[1]), str(row[3]), int(row[4]), row[5], row[6])
        for row in rows if row[2] == "age" and row[3] is not None and int(row[4]) > 0
    ]
    categories = sorted({row[0] for row in age_rows} | {str(row[0]) for row in rows if row[2] == "price"})
    profiles = []
    genders = (("women", "ladieswear"), ("men", "menswear"), ("", "__all__"))
    for category in categories:
        for gender, segment_key in genders:
            age_profile = _build_profiles(
                age_rows, requested_category=category, gender=gender or None,
                mapping_id=mapping["mapping_id"], mapping_version=mapping["mapping_version"],
                source_refs=source_refs,
            )
            segment_rows = [
                row for row in rows
                if row[2] == "price" and str(row[0]).casefold() == category.casefold()
                and str(row[1]).casefold() == segment_key
            ]
            price_record = _price_record(
                segment_rows[0], source_refs=source_refs, mapping=mapping,
                mapping_fingerprint=mapping_fingerprint,
            ) if segment_rows else None
            if age_profile is not None or price_record is not None:
                profiles.append({
                    "category": category, "segment": segment_key,
                    "customer_age_profile": age_profile,
                    "historical_price_aggregate": price_record,
                })
    source_objects = list(source_refs)
    if fabric_source_ref is not None:
        source_objects.append(fabric_source_ref)
    source_identity = {
        "schema_version": SCHEMA_VERSION,
        "source_objects": source_objects, "mapping_version": mapping["mapping_version"],
        "mapping_fingerprint": mapping_fingerprint,
    }
    snapshot_id = hashlib.sha256(
        json.dumps(source_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    snapshot = {
        "snapshot_id": snapshot_id,
        **source_identity,
        "profiles": profiles,
    }
    if fabric_source_ref is not None and fabric_listing_csv is not None:
        snapshot["fabric_source"] = fabric_source_ref
        snapshot["fabric_listing_csv"] = fabric_listing_csv
    return snapshot


def serialize_snapshot(snapshot: dict[str, Any]) -> tuple[bytes, str]:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("The derived H&M aggregate snapshot exceeds its size limit.")
    return payload, hashlib.sha256(payload).hexdigest()


def output_key(snapshot_id: str) -> str:
    if re.fullmatch(r"[a-f0-9]{64}", snapshot_id) is None:
        raise ValueError("Snapshot ID is invalid.")
    return f"{_OUTPUT_PREFIX}/{snapshot_id}.json"


def upload_snapshot(store: R2ObjectStore, *, key: str, payload: bytes, digest: str, snapshot_id: str) -> bool:
    """Publish immutable content; return False when the exact snapshot already exists."""
    client = store._get_client()
    try:
        existing = client.head_object(Bucket=store.settings.bucket, Key=key)
    except Exception as exc:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        code = error.get("Code") if isinstance(error, dict) else None
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode") if isinstance(response, dict) else None
        if code not in {"NoSuchKey", "NotFound", "404"} and status != 404:
            raise RuntimeError(f"Cannot safely inspect derived snapshot destination ({type(exc).__name__}).") from None
    else:
        metadata = existing.get("Metadata", {})
        if isinstance(metadata, dict) and metadata.get("sha256") == digest:
            return False
        raise FileExistsError("A different object already exists at the immutable snapshot key.")
    client.put_object(
        Bucket=store.settings.bucket, Key=key, Body=payload,
        ContentType="application/json",
        Metadata={"sha256": digest, "snapshot-id": snapshot_id, "schema-version": SCHEMA_VERSION},
    )
    return True


def prepare_snapshot(store: R2ObjectStore | None = None) -> tuple[dict[str, Any], bytes, str, str]:
    """Build one request snapshot from one DuckDB query and the bounded fabric CSV."""
    if os.getenv("VOGUE_HM_RESEARCH_NONCOMMERCIAL", "").strip().casefold() not in {"1", "true", "yes"}:
        raise PermissionError("Set VOGUE_HM_RESEARCH_NONCOMMERCIAL=true for the research snapshot job.")
    try:
        import duckdb
    except ImportError:
        raise RuntimeError("The snapshot builder requires the optional duckdb dependency.") from None
    store = store or R2ObjectStore.from_env()
    keys = _source_keys()
    refs = []
    client = store._get_client()
    for logical_name, key in zip(("hm_customers", "hm_articles", "hm_transactions"), keys, strict=True):
        head = client.head_object(Bucket=store.settings.bucket, Key=key)
        size = int(head.get("ContentLength", 0))
        version = _object_version(head)
        if size <= 0 or size > _MAX_SOURCE_BYTES[logical_name] or not version:
            raise ValueError(f"The {logical_name} R2 source has invalid size/version metadata.")
        refs.append({"object_key": key, "version": version})
    fabric_key = r2_source_key("fabric_prices", default=DEFAULT_FABRIC_PRICE_KEY)
    fabric_head = client.head_object(Bucket=store.settings.bucket, Key=fabric_key)
    fabric_size = int(fabric_head.get("ContentLength", 0))
    fabric_version = _object_version(fabric_head)
    if fabric_size <= 0 or fabric_size > _MAX_FABRIC_BYTES or not fabric_version:
        raise ValueError("The fabric listing source has invalid size/version metadata.")
    fabric_bytes, fabric_metadata = store.get_bytes_and_metadata(
        fabric_key, max_bytes=_MAX_FABRIC_BYTES,
    )
    fabric_after = client.head_object(Bucket=store.settings.bucket, Key=fabric_key)
    if _object_version(fabric_after) != fabric_version:
        raise ValueError("The fabric listing source changed while the snapshot was being prepared.")
    fabric_digest = hashlib.sha256(fabric_bytes).hexdigest()
    expected_fabric_digest = fabric_metadata.get("sha256")
    if expected_fabric_digest and expected_fabric_digest.casefold() != fabric_digest:
        raise ValueError("The fabric listing source digest does not match its R2 metadata.")
    fabric_source_ref = {
        "object_key": fabric_key, "version": fabric_version, "sha256": fabric_digest,
    }
    try:
        # Preserve an optional BOM so re-encoding the JSON string reproduces
        # the exact bytes covered by the source SHA-256 at request time.
        fabric_text = fabric_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("The fabric listing source is not valid UTF-8 CSV.") from None
    # Reuse the production CSV schema validator without selecting or exposing
    # listing rows in build output.
    R2FabricPriceSource._analyze_csv(
        fabric_bytes, "", version=fabric_version, sha256=fabric_digest, object_key=fabric_key,
    )
    mapping = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
    mapping_fingerprint = _mapping_digest(mapping)
    rows = _run_aggregate_query(duckdb, store.settings, keys, mapping)
    # DuckDB reads the current object versions through httpfs. Verify they still
    # match the versions recorded before the query so the content-addressed
    # snapshot cannot claim provenance for a different source revision.
    for key, ref in zip(keys, refs, strict=True):
        after = client.head_object(Bucket=store.settings.bucket, Key=key)
        if _object_version(after) != ref["version"]:
            raise ValueError("An H&M source changed while the snapshot was being prepared.")
    snapshot = build_snapshot_payload(rows, source_refs=tuple(refs), mapping=mapping,
                                      mapping_fingerprint=mapping_fingerprint,
                                      fabric_source_ref=fabric_source_ref,
                                      fabric_listing_csv=fabric_text)
    payload, digest = serialize_snapshot(snapshot)
    return snapshot, payload, digest, output_key(snapshot["snapshot_id"])


class HmResearchAggregateSource:
    """Read one versioned aggregate snapshot; keep the API path read-only."""

    def __init__(self, store: R2ObjectStore | None = None, *, object_key: str | None = None):
        self.store = store
        self.object_key = object_key or os.getenv("VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY", "").strip()
        if self.object_key:
            self.object_key = _checked_key(self.object_key, "")

    def analyze(self, product: dict[str, Any]) -> dict[str, Any]:
        try:
            if not self.object_key:
                raise R2ObjectError("The aggregate snapshot key is not configured.")
            store = self.store or R2ObjectStore.from_env()
            mapping = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
            mapping_fingerprint = _mapping_digest(mapping)
            # Snapshot keys include the hash of the source versions and mapping,
            # and the writer never overwrites an existing key. That makes the
            # configured object immutable: a cold process needs one GET only,
            # while later requests reuse the validated process cache.
            cache_key = (store.settings.bucket, self.object_key, mapping_fingerprint)
            with _LOCK:
                snapshot = _CACHE.get(cache_key)
                if snapshot is not None:
                    _CACHE.move_to_end(cache_key)
                pending = None if snapshot is not None else _INFLIGHT.get(cache_key)
                if snapshot is None and pending is None:
                    pending = Future()
                    _INFLIGHT[cache_key] = pending
                    owner = True
                else:
                    owner = False
            if snapshot is None:
                if owner:
                    try:
                        raw, object_metadata = store.get_bytes_and_metadata(
                            self.object_key, max_bytes=_MAX_SNAPSHOT_BYTES,
                        )
                        actual_digest = hashlib.sha256(raw).hexdigest()
                        if object_metadata.get("sha256") != actual_digest:
                            raise R2ObjectError("The aggregate snapshot digest is missing or invalid.")
                        metadata: Any = json.loads(raw.decode("utf-8"))
                        self._validate_snapshot(metadata)
                        if output_key(metadata.get("snapshot_id", "")) != self.object_key:
                            raise ValueError("Aggregate snapshot key does not match its snapshot ID.")
                        if (metadata.get("mapping_version") != mapping.get("mapping_version")
                                or metadata.get("mapping_fingerprint") != mapping_fingerprint):
                            raise ValueError("Aggregate snapshot mapping version is stale.")
                        with _LOCK:
                            _CACHE[cache_key] = metadata
                            _CACHE.move_to_end(cache_key)
                            while len(_CACHE) > _CACHE_MAX_SNAPSHOTS:
                                _CACHE.popitem(last=False)
                        pending.set_result(metadata)
                        snapshot = metadata
                    except BaseException as exc:
                        pending.set_exception(exc)
                        raise
                    finally:
                        with _LOCK:
                            _INFLIGHT.pop(cache_key, None)
                else:
                    snapshot = pending.result()
            return self._select(snapshot, product)
        except Exception as exc:
            category = _failure_category(exc)
            _LOGGER.warning("H&M aggregate snapshot read failed: %s", category)
            message = f"H&M aggregate snapshot could not be read safely ({category})."
            return {
                "customer_age_profile": None, "customer_age_error": message,
                "customer_age_source_status": "error",
                "historical_price": None, "historical_price_error": message,
                "historical_price_source_status": "error",
            }

    @staticmethod
    def _validate_snapshot(value: Any) -> None:
        fabric_source = value.get("fabric_source") if isinstance(value, dict) else None
        if (not isinstance(value, dict)
                or value.get("schema_version") != SCHEMA_VERSION
                or not isinstance(value.get("snapshot_id"), str)
                or not isinstance(value.get("source_objects"), list)
                or not isinstance(value.get("profiles"), list)
                or not isinstance(fabric_source, dict)
                or not all(isinstance(fabric_source.get(field), str) and fabric_source[field].strip()
                           for field in ("object_key", "version", "sha256"))
                or not isinstance(value.get("fabric_listing_csv"), str)):
            raise ValueError("Aggregate snapshot schema is invalid.")
        if not all(isinstance(row, dict) and isinstance(row.get("category"), str)
                   and isinstance(row.get("segment"), str) for row in value["profiles"]):
            raise ValueError("Aggregate snapshot profile rows are invalid.")

    @staticmethod
    def _select(snapshot: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
        mapping = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
        requested_category = str(product.get("category", "")).strip()
        mapped_category = _mapped_requested_category(requested_category, mapping)
        category = requested_category.casefold()
        mapped_category_key = str(mapped_category).casefold()
        gender = (product.get("gender") or "").casefold()
        segment = {"women": "ladieswear", "men": "menswear"}.get(gender, "__all__")
        profiles = snapshot["profiles"]
        selected = next((row for row in profiles if row["category"].casefold() == category
                         and row["segment"].casefold() == segment), None)
        if selected is None and mapped_category_key != category:
            selected = next((row for row in profiles if row["category"].casefold() == mapped_category_key
                             and row["segment"].casefold() == segment), None)
        age_profile = selected.get("customer_age_profile") if selected else None

        product_type = product.get("product_type")
        mapped_price_category = (
            mapping.get("product_type_name", {}).get(product_type or "")
            or _mapped_requested_category(requested_category, mapping)
        )
        price_category_key = str(mapped_price_category).casefold()
        price_segment = {"women": "ladieswear", "men": "menswear"}.get(gender, "__all__")
        price_row = next((row for row in profiles if row["category"].casefold() == price_category_key
                          and row["segment"].casefold() == price_segment), None)
        historical_price = _format_price(
            price_row.get("historical_price_aggregate") if price_row else None,
            requested_category=requested_category, product_type=product_type,
            season=product.get("season"), mapping=mapping,
        )
        customer_age_status = "available" if age_profile is not None else "no_match"
        historical_price_status = "available" if historical_price is not None else "no_match"
        if age_profile is not None and age_profile.get("category", "").casefold() != category:
            age_profile = {**age_profile, "requested_product_type_context": requested_category}
        fabric_prices: tuple[dict[str, Any], ...] = ()
        fabric_price_error = None
        fabric_price_source_status = "not_configured"
        fabric_csv = snapshot.get("fabric_listing_csv")
        fabric_source = snapshot.get("fabric_source")
        if isinstance(fabric_csv, str) and isinstance(fabric_source, dict):
            material = product.get("material")
            if isinstance(material, str) and material.strip():
                try:
                    fabric_bytes = fabric_csv.encode("utf-8")
                    digest = hashlib.sha256(fabric_bytes).hexdigest()
                    if digest != fabric_source.get("sha256"):
                        raise ValueError("fabric snapshot digest mismatch")
                    fabric_prices = R2FabricPriceSource._analyze_csv(
                        fabric_bytes, material.strip(),
                        version=str(fabric_source.get("version", "")),
                        sha256=digest,
                        object_key=str(fabric_source.get("object_key", "")),
                    )
                    fabric_price_source_status = "available" if fabric_prices else "no_match"
                except Exception as exc:
                    _LOGGER.warning("Aggregate fabric data failed validation: %s", type(exc).__name__)
                    fabric_price_error = "Fabric listing data in the aggregate snapshot failed validation."
                    fabric_price_source_status = "error"
        return {
            "customer_age_profile": age_profile, "customer_age_error": None,
            "customer_age_source_status": customer_age_status,
            "historical_price": historical_price, "historical_price_error": None,
            "historical_price_source_status": historical_price_status,
            "fabric_prices": fabric_prices, "fabric_price_error": fabric_price_error,
            "fabric_price_source_status": fabric_price_source_status,
        }


def _format_price(record: dict[str, Any] | None, *, requested_category: str,
                  product_type: str | None, season: str | None, mapping: dict[str, Any]) -> dict[str, Any] | None:
    if record is None:
        return None
    stats = record["price_statistics"]
    scaled = {key: (None if stats[key] is None else stats[key] * 590)
              for key in ("minimum", "p25", "median", "p75", "maximum", "mean")}
    mapped_type = mapping.get("product_type_name", {}).get(product_type or "")
    mapped_type = mapped_type or _mapped_requested_category(requested_category, mapping)
    if mapped_type.casefold() == requested_category.casefold():
        mapped_type = None
    source_refs = record["sources"]
    return {
        "source_id": "hm-kaggle-personalized-fashion-recommendations",
        "source_type": "historical_kaggle_transaction_price_aggregate",
        "permission_status": "non_commercial_research_only", "operational_eligible": False,
        "category": requested_category,
        "mapped_product_type_category": mapped_type,
        "product_type_filter": None, "requested_product_type_context": product_type,
        "season_filter": None,
        "category_mapping": record["category_mapping"],
        "merchandise_segment": (
            "all_mapped_segments" if record["merchandise_segment"] == "__all__"
            else record["merchandise_segment"]
        ),
        "sample_count": record["sample_count"], "article_count": record["article_count"],
        "period": record["period"], "channel_codes": record["channel_codes"],
        "price_statistics": stats | {"unit": "normalized_source_price; currency not provided"},
        "scaled_price_statistics": {
            **scaled, "scale_factor": 590, "currency": "USD",
            "unit": "USD/transaction (×590 scaling convention; not an H&M-published currency label)",
            "method_source": "user-selected USD interpretation of ×590 scaling convention cited by published research",
        },
        "sources": source_refs,
        "limitations": [
            "Historical H&M Kaggle competition data; non-commercial research only, not current market pricing.",
            "The source price is normalized/anonymized. USD is assigned to the ×590 scale per user instruction; Kaggle does not publish this currency mapping as official metadata.",
            "The source has no season field; requested season is retained as context but is not used as a data filter.",
            "This is a transaction-price distribution, not a current listing price, regular price, or sale-price comparison.",
            "Source sales channel IDs remain codes and are not assigned online/store meanings.",
            "Product taxonomy mapping is an approximate crosswalk; unmapped source categories are excluded.",
        ],
    }


def _failure_category(exc: Exception) -> str:
    explicit_code = getattr(exc, "failure_code", None)
    if explicit_code:
        return str(explicit_code)
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error", {})
        code = error.get("Code") if isinstance(error, dict) else None
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"AccessDenied", "Forbidden", "InvalidAccessKeyId", "SignatureDoesNotMatch"} or status == 403:
            return "AccessDenied"
        if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
            return "ObjectNotFound"
    exception_name = type(exc).__name__
    if exception_name in {"EndpointConnectionError", "ConnectionClosedError", "ProxyConnectionError", "SSLError"}:
        return "NetworkConnectionError"
    if exception_name in {"ConnectTimeoutError", "ReadTimeoutError"}:
        return "NetworkTimeout"
    if exception_name in {"NoCredentialsError", "PartialCredentialsError"}:
        return "MissingCredentials"
    message = str(exc).casefold()
    if "timeout" in message or isinstance(exc, TimeoutError):
        return "NetworkTimeout"
    if "access denied" in message or isinstance(exc, PermissionError):
        return "AccessDenied"
    if "json" in message or "snapshot" in message or "mapping" in message:
        return "InvalidSnapshot"
    if any(term in message for term in ("schema", "parquet", "csv", "column", "magic bytes", "delimiter")):
        return "InvalidFormatOrSchema"
    return type(exc).__name__


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build a privacy-safe H&M aggregate snapshot in R2.")
    parser.add_argument("--upload", action="store_true", help="Upload the new content-addressed aggregate object.")
    args = parser.parse_args()
    store = R2ObjectStore.from_env()
    snapshot, payload, digest, key = prepare_snapshot(store)
    print(f"snapshot_id={snapshot['snapshot_id']} profiles={len(snapshot['profiles'])} bytes={len(payload)}")
    print(f"destination={key}")
    if not args.upload:
        print("prepared_only=true; no R2 object was written")
        return
    if os.getenv("VOGUE_HM_AGGREGATE_UPLOAD_ENABLED", "").strip().casefold() not in {"1", "true", "yes"}:
        raise PermissionError("Set VOGUE_HM_AGGREGATE_UPLOAD_ENABLED=true to enable the explicit upload command.")
    uploaded = upload_snapshot(store, key=key, payload=payload, digest=digest,
                               snapshot_id=snapshot["snapshot_id"])
    print("uploaded=" + str(uploaded).lower())


if __name__ == "__main__":
    main()
