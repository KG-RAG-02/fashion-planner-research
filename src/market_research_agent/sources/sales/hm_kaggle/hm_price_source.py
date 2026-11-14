"""Non-commercial research-only price aggregates from H&M Kaggle transactions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from typing import Any
from urllib.parse import urlsplit

from ....sources.shared.r2_object_store import R2ObjectStore, R2Settings
from ....sources.shared.r2_source_manifest import r2_source_key
from .duckdb_http import configure_httpfs
from .hm_customer_age_source import _mapped_requested_category, _safe_failure_category


class HmPriceSourceError(RuntimeError):
    """Safe error raised when the historical Kaggle price source is unavailable."""


_ROOT = Path(__file__).resolve().parents[5]
_CONFIGS_DIR = Path(os.getenv("PROJECT07_CONFIGS_DIR", _ROOT / "configs"))
_MAPPING_PATH = _CONFIGS_DIR / "hm-category-map.v1.json"
_MAX_ARTICLES_BYTES = 50_000_000
_MAX_TRANSACTIONS_BYTES = 1_000_000_000
_CACHE: OrderedDict[tuple[str, ...], dict[str, Any] | None] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_INFLIGHT: dict[tuple[str, ...], Future[dict[str, Any] | None]] = {}
_LOGGER = logging.getLogger(__name__)
_CACHE_MAX_ENTRIES = 256


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _checked_key(value: str, default: str) -> str:
    key = value.strip() or default
    if (key.startswith("/") or ".." in key.split("/")
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}", key) is None):
        raise HmPriceSourceError("An H&M price research object key is invalid.")
    return key


class HmHistoricalPriceResearchSource:
    """Aggregate mapped historical transaction prices directly from R2 parquet."""

    def __init__(self, store: R2ObjectStore | None = None, *,
                 articles_key: str = "", transactions_key: str = "") -> None:
        self.store = store
        self.articles_key = articles_key
        self.transactions_key = transactions_key

    def analyze(self, category: str, *, gender: str | None = None,
                product_type: str | None = None, season: str | None = None) -> dict[str, Any] | None:
        analyze_started = time.perf_counter()
        stage = "configuration"
        if os.getenv("VOGUE_HM_RESEARCH_NONCOMMERCIAL", "").strip().casefold() not in {"1", "true", "yes"}:
            raise HmPriceSourceError(
                "Set VOGUE_HM_RESEARCH_NONCOMMERCIAL=true only for non-commercial research use."
            )
        if not isinstance(category, str) or not category.strip():
            raise HmPriceSourceError("A product category is required for historical price analysis.")
        try:
            import duckdb
        except ImportError:
            raise HmPriceSourceError("H&M price research requires the optional duckdb dependency.") from None
        try:
            store = self.store or R2ObjectStore.from_env()
            stage = "source_manifest"
            articles_key = _checked_key(self.articles_key or r2_source_key(
                "hm_articles", default="articles.parquet",
            ), "articles.parquet")
            transactions_key = _checked_key(
                self.transactions_key or r2_source_key(
                    "hm_transactions", default="transactions_train.parquet",
                ),
                "transactions_train.parquet",
            )
            client = store._get_client()
            object_refs: list[dict[str, str]] = []
            object_sizes = []
            metadata_started = time.perf_counter()
            stage = "object_metadata"
            for key, max_size in ((articles_key, _MAX_ARTICLES_BYTES),
                                  (transactions_key, _MAX_TRANSACTIONS_BYTES)):
                head = client.head_object(Bucket=store.settings.bucket, Key=key)
                size = int(head.get("ContentLength", 0))
                version = str(head.get("VersionId") or head.get("ETag", "")).strip('"')
                if size <= 0 or size > max_size or not version:
                    raise HmPriceSourceError("An H&M price research object has invalid metadata or size.")
                object_sizes.append(size)
                object_refs.append({"object_key": key, "version": version})
            _LOGGER.info("H&M price R2 metadata: objects=2 declared_bytes=%d elapsed=%.3fs",
                         sum(object_sizes), time.perf_counter() - metadata_started)
            stage = "category_mapping"
            mapping = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
            mapping_digest = hashlib.sha256(
                json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            versions = tuple(ref["version"] for ref in object_refs)
            cache_key = (store.settings.bucket, articles_key, transactions_key, *versions,
                         mapping["mapping_version"], mapping_digest,
                         category.strip().casefold(), (gender or "").strip().casefold(),
                         (product_type or "").strip().casefold(), (season or "").strip().casefold())
            with _CACHE_LOCK:
                if cache_key in _CACHE:
                    cached_value = _CACHE[cache_key]
                    _CACHE.move_to_end(cache_key)
                    pending = None
                    owner = False
                    _LOGGER.info("H&M price aggregate cache hit")
                else:
                    cached_value = None
                    pending = _CACHE_INFLIGHT.get(cache_key)
                    if pending is None:
                        pending = Future()
                        _CACHE_INFLIGHT[cache_key] = pending
                        owner = True
                    else:
                        owner = False
            if pending is None:
                _LOGGER.info("H&M price analysis completed from cache in %.3fs",
                             time.perf_counter() - analyze_started)
                return cached_value
            if owner:
                try:
                    stage = "duckdb_httpfs_and_aggregate"
                    query_started = time.perf_counter()
                    cached_value = self._query(
                        duckdb, store.settings, (articles_key, transactions_key), versions, mapping,
                        mapping_digest, category.strip(), gender, product_type, season,
                    )
                    _LOGGER.info("H&M price DuckDB aggregate completed in %.3fs matched=%s",
                                 time.perf_counter() - query_started, cached_value is not None)
                    with _CACHE_LOCK:
                        _CACHE[cache_key] = cached_value
                        _CACHE.move_to_end(cache_key)
                        while len(_CACHE) > _CACHE_MAX_ENTRIES:
                            _CACHE.popitem(last=False)
                    pending.set_result(cached_value)
                except BaseException as exc:
                    pending.set_exception(exc)
                    raise
                finally:
                    with _CACHE_LOCK:
                        _CACHE_INFLIGHT.pop(cache_key, None)
            else:
                cached_value = pending.result()
                _LOGGER.info("H&M price analysis shared in-flight result")
            _LOGGER.info("H&M price analysis completed in %.3fs", time.perf_counter() - analyze_started)
            return cached_value
        except HmPriceSourceError as exc:
            category = _safe_failure_category(exc)
            _LOGGER.warning("H&M historical price source failed: stage=%s category=%s", stage, category)
            raise HmPriceSourceError(f"{exc} (stage={stage}; code={category}).") from None
        except Exception as exc:
            category = _safe_failure_category(exc)
            _LOGGER.warning("H&M historical price source failed: stage=%s category=%s", stage, category)
            raise HmPriceSourceError(
                f"Historical H&M price aggregates could not be read safely from R2 (stage={stage}; code={category})."
            ) from None

    @staticmethod
    def _query(duckdb: Any, settings: R2Settings, keys: tuple[str, str], versions: tuple[str, str],
               mapping: dict[str, Any], mapping_digest: str, requested_category: str,
               gender: str | None, product_type: str | None, season: str | None) -> dict[str, Any] | None:
        conn = duckdb.connect()
        try:
            extension_dir = os.getenv("VOGUE_DUCKDB_EXTENSION_DIRECTORY", "").strip()
            if extension_dir:
                conn.execute("SET extension_directory = " + _sql_string(extension_dir))
            try:
                conn.execute("LOAD httpfs")
            except Exception:
                raise HmPriceSourceError(
                    "DuckDB httpfs must be installed in the runtime before R2 price research can run."
                ) from None
            try:
                configure_httpfs(conn)
            except Exception as exc:
                raise HmPriceSourceError(
                    f"DuckDB HTTP timeout configuration failed ({type(exc).__name__})."
                ) from None
            endpoint = urlsplit(settings.endpoint).netloc if settings.endpoint else f"{settings.account_id}.r2.cloudflarestorage.com"
            conn.execute(
                "CREATE SECRET hm_price_research_r2 (TYPE S3, KEY_ID " + _sql_string(settings.access_key_id)
                + ", SECRET " + _sql_string(settings.secret_access_key) + ", ENDPOINT " + _sql_string(endpoint)
                + ", REGION 'auto', URL_STYLE 'path', USE_SSL true)"
            )
            conn.execute("CREATE TEMP TABLE hm_category_map (source_value VARCHAR, dimension VARCHAR, mapped_category VARCHAR)")
            map_rows = [(key, "type", value) for key, value in mapping["product_type_name"].items()]
            map_rows.extend((key, "group", value) for key, value in mapping["product_group_name"].items())
            conn.executemany("INSERT INTO hm_category_map VALUES (?, ?, ?)", map_rows)
            articles_uri = _sql_string(f"s3://{settings.bucket}/{keys[0]}")
            transactions_uri = _sql_string(f"s3://{settings.bucket}/{keys[1]}")
            mapped_product_type = mapping.get("product_type_name", {}).get(product_type or "")
            query_category = mapped_product_type or _mapped_requested_category(requested_category, mapping)
            expected_segment = {"women": "ladieswear", "men": "menswear"}.get(
                (gender or "").strip().casefold()
            )
            row = conn.execute(f"""
                WITH mapped_articles AS (
                    SELECT a.article_id, a.index_group_name, a.product_type_name,
                           COALESCE(type_map.mapped_category, group_map.mapped_category) AS category
                    FROM read_parquet({articles_uri}) AS a
                    LEFT JOIN hm_category_map AS type_map
                      ON type_map.dimension = 'type'
                     AND lower(trim(a.product_type_name)) = lower(trim(type_map.source_value))
                    LEFT JOIN hm_category_map AS group_map
                      ON group_map.dimension = 'group'
                     AND lower(trim(a.product_group_name)) = lower(trim(group_map.source_value))
                )
                SELECT a.category,
                       COUNT(*)::BIGINT AS transaction_rows,
                       COUNT(DISTINCT t.article_id)::BIGINT AS article_count,
                       MIN(t.price), quantile_cont(t.price, 0.25), quantile_cont(t.price, 0.5),
                       quantile_cont(t.price, 0.75), MAX(t.price), AVG(t.price),
                       MIN(TRY_CAST(t.t_dat AS DATE))::VARCHAR,
                       MAX(TRY_CAST(t.t_dat AS DATE))::VARCHAR,
                       list(DISTINCT CAST(t.sales_channel_id AS VARCHAR))
                FROM read_parquet({transactions_uri}) AS t
                INNER JOIN mapped_articles AS a USING (article_id)
                WHERE a.category IS NOT NULL AND t.price IS NOT NULL
                  AND lower(a.category) = lower(?)
                  AND (? IS NULL OR lower(COALESCE(a.index_group_name, '')) = lower(?))
                GROUP BY 1
            """, [query_category, expected_segment, expected_segment]).fetchone()
            if row is None:
                return None
            transaction_rows = int(row[1])
            channels = sorted(str(channel) for channel in (row[11] or []))
            return {
                "source_id": "hm-kaggle-personalized-fashion-recommendations",
                "source_type": "historical_kaggle_transaction_price_aggregate",
                "permission_status": "non_commercial_research_only",
                "operational_eligible": False,
                "category": requested_category,
                "mapped_product_type_category": (
                    query_category if query_category.strip().casefold() != requested_category.strip().casefold()
                    else None
                ),
                "product_type_filter": None,
                "requested_product_type_context": product_type,
                "season_filter": None,
                "category_mapping": {
                    "mapping_id": mapping["mapping_id"],
                    "mapping_version": mapping["mapping_version"],
                    "mapping_fingerprint": mapping_digest,
                    "matching": "product_type_exact_then_product_group_fallback",
                },
                "merchandise_segment": expected_segment or "all_mapped_segments",
                "sample_count": transaction_rows,
                "article_count": int(row[2]),
                "period": {"start": str(row[9]), "end": str(row[10])},
                "channel_codes": channels,
                "price_statistics": {
                    "minimum": float(row[3]),
                    "p25": float(row[4]),
                    "median": float(row[5]),
                    "p75": float(row[6]),
                    "maximum": float(row[7]),
                    "mean": float(row[8]),
                    "currency": None,
                    "unit": "normalized_source_price; currency not provided",
                    "statistic": "transaction_price_distribution; unweighted transaction rows",
                },
                "scaled_price_statistics": {
                    "scale_factor": 590,
                    "minimum": float(row[3]) * 590,
                    "p25": float(row[4]) * 590,
                    "median": float(row[5]) * 590,
                    "p75": float(row[6]) * 590,
                    "maximum": float(row[7]) * 590,
                    "mean": float(row[8]) * 590,
                    "currency": "USD",
                    "unit": "USD/transaction (×590 scaling convention; not an H&M-published currency label)",
                    "method_source": "user-selected USD interpretation of ×590 scaling convention cited by published research",
                },
                "sources": [
                    {"object_key": key, "version": version}
                    for key, version in zip(keys, versions, strict=True)
                ],
                "limitations": [
                    "Historical H&M Kaggle competition data; non-commercial research only, not current market pricing.",
                    "The source price is normalized/anonymized. USD is assigned to the ×590 scale per user instruction; Kaggle does not publish this currency mapping as official metadata.",
                    "The source has no season field; requested season is retained as context but is not used as a data filter.",
                    "This is a transaction-price distribution, not a current listing price, regular price, or sale-price comparison.",
                    "Source sales channel IDs remain codes and are not assigned online/store meanings.",
                    "Product taxonomy mapping is an approximate crosswalk; unmapped source categories are excluded.",
                ],
            }
        finally:
            conn.close()
