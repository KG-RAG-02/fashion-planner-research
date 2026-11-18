"""Non-commercial research-only age-band profiles from H&M Kaggle observations.

The query returns category-level distinct-customer aggregates only. It never
returns or logs customer identifiers or row-level customer records.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from difflib import SequenceMatcher
import threading
import logging
import time
from collections import OrderedDict
from concurrent.futures import Future
from typing import Any
from urllib.parse import urlsplit

from ....sources.shared.r2_object_store import R2ObjectStore, R2Settings
from ....sources.shared.r2_source_manifest import r2_source_key
from ....resource_paths import CONFIGS_DIR
from ....runtime_settings import hm_research_noncommercial_enabled
from .duckdb_http import configure_httpfs


class CustomerAgeSourceError(RuntimeError):
    """Safe, non-sensitive age-profile source error."""


_MAPPING_PATH = CONFIGS_DIR / "hm-category-map.json"
_CACHE: OrderedDict[tuple[str, ...], dict[tuple[str, str], dict[str, Any]]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_INFLIGHT: dict[tuple[str, ...], Future[dict[tuple[str, str], dict[str, Any]]]] = {}
_LOGGER = logging.getLogger(__name__)
_MIN_CELL_SIZE = 10
_CACHE_MAX_SOURCE_VERSIONS = 4
_CSV_COLUMNS = (
    "'customer_id': 'VARCHAR', 'FN': 'DOUBLE', 'Active': 'DOUBLE', "
    "'club_member_status': 'VARCHAR', 'fashion_news_frequency': 'VARCHAR', "
    "'age': 'INTEGER', 'postal_code': 'VARCHAR'"
)


def _configure_age_duckdb(conn: Any) -> None:
    """Bound DuckDB concurrency and point spill files at the writable cache volume."""
    threads_raw = os.getenv("RESEARCH_AGE_DUCKDB_THREADS", "1").strip()
    if re.fullmatch(r"[1-9][0-9]{0,2}", threads_raw) is None:
        raise CustomerAgeSourceError("DuckDB age-query thread configuration is invalid.")
    memory_limit = os.getenv("RESEARCH_AGE_DUCKDB_MEMORY_LIMIT", "3GB").strip()
    if re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?(?:GB|GIB|MB|MIB)", memory_limit, re.IGNORECASE) is None:
        raise CustomerAgeSourceError("DuckDB age-query memory configuration is invalid.")
    temp_directory = os.getenv(
        "RESEARCH_AGE_DUCKDB_TEMP_DIRECTORY",
        os.getenv("RESEARCH_CACHE_DIR", "/tmp") + "/duckdb-age-tmp",
    ).strip()
    if not temp_directory or "\x00" in temp_directory:
        raise CustomerAgeSourceError("DuckDB age-query temporary directory is invalid.")
    try:
        Path(temp_directory).mkdir(parents=True, exist_ok=True)
    except OSError:
        raise CustomerAgeSourceError("DuckDB age-query temporary directory is not writable.") from None
    conn.execute("SET threads = " + threads_raw)
    conn.execute("SET memory_limit = " + _sql_string(memory_limit))
    conn.execute("SET preserve_insertion_order = false")
    conn.execute("SET temp_directory = " + _sql_string(temp_directory))
    _LOGGER.info("H&M age DuckDB config: threads=%s memory_limit=%s temp_directory=configured",
                 threads_raw, memory_limit)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _checked_key(value: str, default: str) -> str:
    key = value.strip() or default
    if (key.startswith("/") or ".." in key.split("/")
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}", key) is None):
        raise CustomerAgeSourceError("An H&M research object key is invalid.")
    return key


def _mapped_requested_category(category: str, mapping: dict[str, Any]) -> str:
    """Map a category to the closest H&M label, retaining match provenance."""
    return _map_requested_category(category, mapping)["mapped_category"]


_CATEGORY_SIMILARITY_THRESHOLD = 0.52


def _category_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _map_requested_category(category: str, mapping: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic exact/alias/fuzzy mapping metadata for a category."""
    requested = category.strip()
    key = category.strip().casefold()
    for alias, mapped in mapping.get("requested_category_aliases", {}).items():
        if isinstance(alias, str) and isinstance(mapped, str) and alias.strip().casefold() == key:
            return {"requested_category": requested, "mapped_category": mapped,
                    "method": "explicit_alias", "score": 1.0, "threshold": _CATEGORY_SIMILARITY_THRESHOLD}
    for product_type, mapped in mapping.get("product_type_name", {}).items():
        if isinstance(product_type, str) and isinstance(mapped, str) and product_type.strip().casefold() == key:
            return {"requested_category": requested, "mapped_category": mapped,
                    "method": "exact_product_type", "score": 1.0, "threshold": _CATEGORY_SIMILARITY_THRESHOLD}

    candidates: dict[str, set[str]] = {}
    for alias, mapped in mapping.get("requested_category_aliases", {}).items():
        if isinstance(alias, str) and isinstance(mapped, str):
            candidates.setdefault(mapped, set()).add(alias)
    for source, mapped in mapping.get("product_type_name", {}).items():
        if isinstance(source, str) and isinstance(mapped, str):
            candidates.setdefault(mapped, set()).add(source)
    for source, mapped in mapping.get("product_group_name", {}).items():
        if isinstance(source, str) and isinstance(mapped, str):
            candidates.setdefault(mapped, set()).add(source)

    normalized = _category_key(requested)
    scored: list[tuple[float, str, str]] = []
    for mapped, labels in candidates.items():
        labels.add(mapped)
        best = max(
            ((SequenceMatcher(None, normalized, _category_key(label)).ratio(), label)
             for label in labels if _category_key(label)),
            default=(0.0, ""),
        )
        scored.append((best[0], mapped, best[1]))
    if not scored:
        return {"requested_category": requested, "mapped_category": requested,
                "method": "unmapped", "score": 0.0, "threshold": _CATEGORY_SIMILARITY_THRESHOLD}
    score, mapped, matched_label = max(scored, key=lambda item: (item[0], item[1]))
    if score < _CATEGORY_SIMILARITY_THRESHOLD:
        return {"requested_category": requested, "mapped_category": requested,
                "method": "below_threshold", "score": round(score, 4),
                "matched_label": matched_label, "suggested_category": mapped,
                "threshold": _CATEGORY_SIMILARITY_THRESHOLD}
    return {"requested_category": requested, "mapped_category": mapped,
            "method": "fuzzy_similarity", "score": round(score, 4),
            "matched_label": matched_label, "threshold": _CATEGORY_SIMILARITY_THRESHOLD}


def _map_product_type(value: str | None, mapping: dict[str, Any]) -> dict[str, Any]:
    requested = value.strip() if isinstance(value, str) else None
    if not requested:
        return {"requested": value, "mapped": None, "method": "not_provided", "score": None}
    candidates = {str(label): str(mapped) for label, mapped in mapping.get("product_type_name", {}).items()}
    exact_label = next((label for label in candidates if label.casefold() == requested.casefold()), None)
    if exact_label:
        return {"requested": requested, "mapped": exact_label, "mapped_category": candidates[exact_label],
                "method": "exact", "score": 1.0}
    query = _category_key(requested)
    score, label, mapped = max(
        ((SequenceMatcher(None, query, _category_key(label)).ratio(), label, mapped)
         for label, mapped in candidates.items()), default=(0.0, "", ""),
    )
    if score < _CATEGORY_SIMILARITY_THRESHOLD:
        return {"requested": requested, "mapped": None, "method": "below_threshold",
                "score": round(score, 4), "suggested": label or None,
                "suggested_category": mapped or None, "threshold": _CATEGORY_SIMILARITY_THRESHOLD}
    return {"requested": requested, "mapped": label, "mapped_category": mapped,
            "method": "fuzzy_similarity", "score": round(score, 4),
            "matched_label": label, "threshold": _CATEGORY_SIMILARITY_THRESHOLD}


def _map_gender(value: str | None) -> dict[str, Any]:
    requested = value.strip() if isinstance(value, str) else None
    if not requested:
        return {"requested": value, "mapped": None, "merchandise_segment": None,
                "method": "not_provided", "filter_applied": False}
    aliases = {
        "women": ("women", "woman", "female", "ladies", "ladieswear", "여성", "여자", "여성복"),
        "men": ("men", "man", "male", "menswear", "남성", "남자", "남성복"),
        "unisex": ("unisex", "공용", "유니섹스", "남녀공용"),
        "unknown": ("unknown", "미상", "모름"),
    }
    key = requested.casefold()
    for canonical, labels in aliases.items():
        if key in labels:
            segment = {"women": "ladieswear", "men": "menswear"}.get(canonical)
            return {"requested": requested, "mapped": canonical, "merchandise_segment": segment,
                    "method": "exact_alias", "filter_applied": segment is not None}
    return {"requested": requested, "mapped": None, "merchandise_segment": None,
            "method": "unsupported", "filter_applied": False}


def _safe_failure_category(exc: Exception) -> str:
    """Map SDK/SQL failures to a small safe vocabulary without logging raw text."""
    explicit_code = getattr(exc, "failure_code", None)
    if explicit_code in {
        "AccessDenied", "ObjectNotFound", "NetworkTimeout", "NetworkTimeoutOrThrottled",
        "R2ServiceUnavailable", "R2ReadFailed", "ObjectSizeLimitExceeded", "ObjectSizeMismatch",
    }:
        return explicit_code
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error", {})
        code = str(error.get("Code", "")) if isinstance(error, dict) else ""
        metadata = response.get("ResponseMetadata", {})
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        if code in {"AccessDenied", "Forbidden", "InvalidAccessKeyId", "SignatureDoesNotMatch"} or status == 403:
            return "AccessDenied"
        if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
            return "ObjectNotFound"
        if code in {"RequestTimeout", "RequestTimeoutException", "SlowDown"}:
            return "NetworkTimeoutOrThrottled"
    exception_name = type(exc).__name__
    if exception_name in {"EndpointConnectionError", "ConnectionClosedError", "ProxyConnectionError", "SSLError"}:
        return "NetworkConnectionError"
    if exception_name in {"ConnectTimeoutError", "ReadTimeoutError"}:
        return "NetworkTimeout"
    if exception_name in {"NoCredentialsError", "PartialCredentialsError"}:
        return "MissingCredentials"
    message = str(exc).casefold()
    if "exceeds its allowed size" in message or "size bound" in message:
        return "ObjectSizeLimitExceeded"
    if "invalid" in message and "key" in message:
        return "InvalidObjectKey"
    if "metadata" in message or "version" in message:
        return "InvalidObjectMetadata"
    if "manifest" in message or "mapping" in message:
        return "InvalidSourceConfiguration"
    if "timeout configuration" in message or "http timeout" in message:
        return "DuckDBConfigurationError"
    if "httpfs" in message or "extension" in message:
        return "DuckDBExtensionUnavailable"
    if any(term in message for term in ("schema", "parquet", "csv", "column", "magic bytes", "delimiter")):
        return "InvalidFormatOrSchema"
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(term in message for term in ("timed out", "timeout", "connection reset", "connection refused")):
        return "NetworkTimeout"
    if any(term in message for term in ("httpfs", "extension not found", "extension installation", "autoload")):
        return "DuckDBExtensionUnavailable"
    if isinstance(exc, PermissionError) or any(term in message for term in ("access denied", "forbidden", "403")):
        return "AccessDenied"
    if any(term in message for term in ("no magic bytes", "parquet", "csv", "column", "schema", "delimiter")):
        return "InvalidFormatOrSchema"
    if any(term in message for term in ("no such key", "not found", "404")):
        return "ObjectNotFound"
    return type(exc).__name__


def _mapping_sha256(mapping: dict[str, Any]) -> str:
    """카테고리 매핑 내용의 SHA-256. 매핑의 버전 대신 이 값으로 식별한다."""
    return hashlib.sha256(
        json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _build_profiles(
    rows: list[tuple[Any, ...]], *, requested_category: str, gender: str | None,
    mapping_id: str, mapping_sha256: str, source_refs: tuple[dict[str, str], ...],
) -> dict[str, Any] | None:
    candidates = [row for row in rows if str(row[0]).strip().casefold() == requested_category.strip().casefold()]
    gender_key = (gender or "").casefold()
    expected_group = {"women": "ladieswear", "men": "menswear"}.get(gender_key, "__all__")
    candidates = [row for row in candidates if str(row[1] or "").strip().casefold() == expected_group]
    if not candidates:
        return None
    # Each input row is one already-aggregated category × age-band cell.
    period_starts = [str(row[4]) for row in candidates if row[4] is not None]
    period_ends = [str(row[5]) for row in candidates if row[5] is not None]
    counts = {str(row[2]): int(row[3]) for row in candidates}
    denominator = sum(counts.values())
    if denominator <= 0:
        return None
    known_age_total = sum(counts.get(band, 0) for band in ("under_20", "20_29", "30_39", "40_49", "50_plus"))
    cohort_counts = {
        "under_20": counts.get("under_20", 0),
        "20_39": counts.get("20_29", 0) + counts.get("30_39", 0),
        "40_plus": counts.get("40_49", 0) + counts.get("50_plus", 0),
    }
    target_cohort = max(cohort_counts, key=cohort_counts.get) if known_age_total else None
    target_count = cohort_counts.get(target_cohort, 0) if target_cohort else 0
    small_cell_present = any(0 < count < _MIN_CELL_SIZE for count in counts.values())
    if small_cell_present:
        target_cohort = None
        target_count = 0
    target_suppressed = small_cell_present or 0 < target_count < _MIN_CELL_SIZE
    age_bands = []
    for band in ("under_20", "20_29", "30_39", "40_49", "50_plus", "unknown_age"):
        count = counts.get(band, 0)
        suppressed = small_cell_present or 0 < count < _MIN_CELL_SIZE
        age_bands.append({
            "age_band": band,
            "unique_customer_count": None if suppressed else count,
            "share": None if suppressed else round(count / denominator, 6),
            "suppressed": suppressed,
            "suppression_threshold": _MIN_CELL_SIZE,
        })
    return {
        "source_id": "hm-kaggle-personalized-fashion-recommendations",
        "source_type": "historical_kaggle_customer_article_transaction_aggregate",
        "permission_status": "non_commercial_research_only",
        "operational_eligible": False,
        "category": requested_category,
        "category_mapping": {"mapping_id": mapping_id, "mapping_sha256": mapping_sha256,
                             "matching": "product_type_exact_then_product_group_fallback"},
        "product_merchandise_segment": expected_group if expected_group != "__all__" else "all_mapped_segments",
        "unique_customer_denominator": None if small_cell_present else denominator,
        "sample_size_suppressed": small_cell_present,
        "target_age_cohort": {
            "age_band": target_cohort,
            "definition": {"under_20": "16–19", "20_39": "20–39 (20s and 30s)", "40_plus": "40 and over"}.get(target_cohort),
            "unique_customer_count": None if target_suppressed else target_count,
            "share_of_customers_with_known_age": (
                None if target_suppressed or not known_age_total else round(target_count / known_age_total, 6)
            ),
            "suppressed": target_suppressed,
            "selection": "largest of the predefined <20, 20–39, and 40+ cohorts",
        },
        "age_bands": age_bands,
        "period": {"start": min(period_starts) if period_starts else None,
                   "end": max(period_ends) if period_ends else None},
        "age_semantics": "customer age recorded in the dataset snapshot; not age at each historical transaction date",
        "aggregation": "distinct customer_id per mapped product category and age band; one customer counted once within each category",
        "sources": list(source_refs),
        "limitations": [
            "Historical H&M Kaggle competition data; research-only and not current customer behavior or demand.",
            "Customer records have no gender field; women/men filters use the purchased article merchandise segment.",
            "Age bands under the minimum cell size are suppressed.",
            "Product taxonomy mappings are approximate crosswalks; unmapped article types are excluded.",
        ],
    }


@dataclass(frozen=True, slots=True)
class HmCustomerAgeResearchSource:
    """Read-only R2 source, disabled unless non-commercial research is explicit."""

    store: R2ObjectStore | None = None
    customers_key: str = ""
    articles_key: str = ""
    transactions_key: str = ""

    def profile(self, category: str, *, gender: str | None = None,
                product_type: str | None = None) -> dict[str, Any] | None:
        profile_started = time.perf_counter()
        if not hm_research_noncommercial_enabled():
            raise CustomerAgeSourceError("H&M age research is disabled by runtime settings.")
        if not isinstance(category, str) or not category.strip():
            raise CustomerAgeSourceError("A product category is required for age-band analysis.")
        try:
            import duckdb
        except ImportError:
            raise CustomerAgeSourceError("H&M age research requires the optional duckdb dependency.") from None
        try:
            store = self.store or R2ObjectStore.from_env()
        except Exception:
            _LOGGER.warning("H&M customer age source failed: stage=r2_configuration category=R2ConfigurationError")
            raise CustomerAgeSourceError(
                "R2 research access is not configured safely (stage=r2_configuration; code=R2ConfigurationError)."
            ) from None
        stage = "source_manifest"
        try:
            customers_key = _checked_key(self.customers_key or r2_source_key(
                "hm_customers", default="customers.csv",
            ), "customers.csv")
            articles_key = _checked_key(self.articles_key or r2_source_key(
                "hm_articles", default="articles.parquet",
            ), "articles.parquet")
            transactions_key = _checked_key(self.transactions_key or r2_source_key(
                "hm_transactions", default="transactions_train.parquet",
            ), "transactions_train.parquet")
        except Exception as exc:
            category = _safe_failure_category(exc)
            _LOGGER.warning("H&M customer age source failed: stage=%s category=%s", stage, category)
            raise CustomerAgeSourceError(
                f"H&M research source manifest could not be resolved safely (stage={stage}; code={category})."
            ) from None
        keys = (customers_key, articles_key, transactions_key)
        stage = "object_metadata"
        try:
            client = store._get_client()
            versions = []
            object_sizes = []
            max_sizes = {customers_key: 300_000_000, articles_key: 50_000_000, transactions_key: 1_000_000_000}
            metadata_started = time.perf_counter()
            for key in keys:
                head = client.head_object(Bucket=store.settings.bucket, Key=key)
                size = int(head.get("ContentLength", 0))
                version = str(head.get("VersionId") or head.get("ETag", "")).strip('"')
                if size <= 0 or size > max_sizes[key]:
                    raise CustomerAgeSourceError("An H&M research object exceeds its allowed size bound.")
                if not version:
                    raise CustomerAgeSourceError("An H&M research object has invalid version metadata.")
                object_sizes.append(size)
                versions.append(version)
            _LOGGER.info("H&M age R2 metadata: objects=3 declared_bytes=%d elapsed=%.3fs",
                         sum(object_sizes), time.perf_counter() - metadata_started)
            stage = "category_mapping"
            mapping = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
            mapping_digest = _mapping_sha256(mapping)
            cache_key = tuple((store.settings.bucket, *keys, *versions, mapping_digest))
            while True:
                with _CACHE_LOCK:
                    if cache_key in _CACHE:
                        aggregate_map = _CACHE[cache_key]
                        _CACHE.move_to_end(cache_key)
                        _LOGGER.info("H&M age aggregate cache hit")
                        break
                    pending = _CACHE_INFLIGHT.get(cache_key)
                    if pending is None:
                        pending = Future()
                        _CACHE_INFLIGHT[cache_key] = pending
                        owner = True
                    else:
                        owner = False
                if owner:
                    try:
                        query_started = time.perf_counter()
                        stage = "duckdb_httpfs_and_aggregate"
                        aggregate_map = self._query_aggregates(
                            duckdb, store.settings, keys, mapping, tuple(versions),
                        )
                        _LOGGER.info("H&M age DuckDB aggregate completed in %.3fs categories=%d",
                                     time.perf_counter() - query_started, len(aggregate_map))
                        with _CACHE_LOCK:
                            _CACHE[cache_key] = aggregate_map
                            _CACHE.move_to_end(cache_key)
                            while len(_CACHE) > _CACHE_MAX_SOURCE_VERSIONS:
                                _CACHE.popitem(last=False)
                        pending.set_result(aggregate_map)
                    except BaseException as exc:
                        pending.set_exception(exc)
                        raise
                    finally:
                        with _CACHE_LOCK:
                            _CACHE_INFLIGHT.pop(cache_key, None)
                    break
                aggregate_map = pending.result()
                break
            gender_mapping = _map_gender(gender)
            gender_key = gender_mapping.get("mapped") or ""
            if gender_key not in {"women", "men"}:
                gender_key = ""
            category_mapping = _map_requested_category(category, mapping)
            product_type_mapping = _map_product_type(product_type, mapping)
            mapped_category = category_mapping["mapped_category"]
            if product_type_mapping.get("mapped"):
                # The aggregate map includes exact product-type aliases. Prefer this
                # key for specific requests, then retain the category as fallback.
                profile = aggregate_map.get((str(product_type_mapping["mapped"]).casefold(), gender_key))
                if profile is None:
                    profile = aggregate_map.get((str(product_type_mapping.get("mapped_category") or mapped_category).casefold(), gender_key))
                lookup_method = "exact_product_type" if profile is not None else "product_type_no_match"
            else:
                profile = aggregate_map.get((mapped_category.casefold(), gender_key))
                lookup_method = "category_fallback"
            if profile is not None:
                profile = {**profile,
                           "requested_product_type_context": product_type or category.strip(),
                           "requested_product_category": category.strip(),
                           "requested_product_type": product_type,
                           "category_mapping": category_mapping,
                           "product_type_mapping": product_type_mapping,
                           "lookup_method": lookup_method,
                           "requested_gender": gender,
                           "gender_mapping": gender_mapping,
                           "gender_aggregate_scope": (
                               "women" if gender_key == "women" else
                               "men" if gender_key == "men" else "all_merchandise_segments"
                           ),
                           "gender_filter_applied": gender_key in {"women", "men"},
                           "gender_fallback_applied": False}
            if profile is None:
                _LOGGER.info("H&M age profile completed with no mapped result in %.3fs",
                             time.perf_counter() - profile_started)
                return None
            _LOGGER.info("H&M age profile completed in %.3fs", time.perf_counter() - profile_started)
            return profile
        except CustomerAgeSourceError as exc:
            category = _safe_failure_category(exc)
            _LOGGER.warning("H&M customer age source failed: stage=%s category=%s", stage, category)
            raise CustomerAgeSourceError(
                f"{exc} (stage={stage}; code={category})."
            ) from None
        except Exception as exc:
            # Keep SDK/SQL detail out of logs and user responses; exception class
            # distinguishes missing objects, access failures, timeouts and parser errors.
            category = _safe_failure_category(exc)
            _LOGGER.warning("H&M customer age source failed: stage=%s category=%s", stage, category)
            raise CustomerAgeSourceError(
                f"H&M research aggregates could not be read safely from R2 (stage={stage}; code={category})."
            ) from None

    @staticmethod
    def _query_aggregates(duckdb: Any, settings: R2Settings, keys: tuple[str, ...], mapping: dict[str, Any], versions: tuple[str, ...]) -> dict[tuple[str, str], dict[str, Any]]:
        conn = duckdb.connect()
        query_started = time.perf_counter()
        try:
            _configure_age_duckdb(conn)
            extension_dir = os.getenv("RESEARCH_DUCKDB_EXTENSION_DIRECTORY", "").strip()
            if extension_dir:
                conn.execute("SET extension_directory = " + _sql_string(extension_dir))
            try:
                conn.execute("LOAD httpfs")
            except Exception as exc:
                _LOGGER.warning("DuckDB httpfs load failed: %s", _safe_failure_category(exc))
                raise CustomerAgeSourceError(
                    f"DuckDB httpfs must be installed in the runtime before R2 age research can run ({_safe_failure_category(exc)})."
                ) from None
            try:
                configure_httpfs(conn)
            except Exception as exc:
                raise CustomerAgeSourceError(
                    f"DuckDB HTTP timeout configuration failed ({type(exc).__name__})."
                ) from None
            endpoint = urlsplit(settings.endpoint).netloc if settings.endpoint else f"{settings.account_id}.r2.cloudflarestorage.com"
            conn.execute(
                "CREATE SECRET customer_research_r2 (TYPE S3, KEY_ID " + _sql_string(settings.access_key_id)
                + ", SECRET " + _sql_string(settings.secret_access_key) + ", ENDPOINT " + _sql_string(endpoint)
                + ", REGION 'auto', URL_STYLE 'path', USE_SSL true)"
            )
            conn.execute("CREATE TEMP TABLE hm_category_map (source_value VARCHAR, dimension VARCHAR, mapped_category VARCHAR)")
            map_rows = [(key, "type", value) for key, value in mapping["product_type_name"].items()]
            map_rows.extend((key, "group", value) for key, value in mapping["product_group_name"].items())
            conn.executemany("INSERT INTO hm_category_map VALUES (?, ?, ?)", map_rows)
            customers_uri = _sql_string(f"s3://{settings.bucket}/{keys[0]}")
            articles_uri = _sql_string(f"s3://{settings.bucket}/{keys[1]}")
            transactions_uri = _sql_string(f"s3://{settings.bucket}/{keys[2]}")
            customer_schema = "{" + _CSV_COLUMNS + "}"
            # Materialize the large remote inputs in DuckDB's spill-backed
            # temp storage before joining. A single remote scan/join/COUNT
            # query held both Parquet scan buffers and distinct hash state;
            # splitting it keeps those peaks below the container limit.
            conn.execute(f"""
                CREATE TEMP TABLE hm_age_transactions AS
                SELECT customer_id, article_id, t_dat FROM read_parquet({transactions_uri})
            """)
            conn.execute(f"""
                CREATE TEMP TABLE hm_age_articles AS
                SELECT a.article_id, a.index_group_name,
                           COALESCE(type_map.mapped_category, group_map.mapped_category) AS category
                    FROM read_parquet({articles_uri}) AS a
                    LEFT JOIN hm_category_map AS type_map
                      ON type_map.dimension = 'type'
                     AND lower(trim(a.product_type_name)) = lower(trim(type_map.source_value))
                    LEFT JOIN hm_category_map AS group_map
                      ON group_map.dimension = 'group'
                     AND lower(trim(a.product_group_name)) = lower(trim(group_map.source_value))
            """)
            conn.execute(f"""
                CREATE TEMP TABLE hm_age_customers AS
                SELECT customer_id, age FROM read_csv(
                        {customers_uri}, header=true, delim=',', columns={customer_schema}, sample_size=1000
                    )
                WHERE customer_id IS NOT NULL AND trim(customer_id) <> ''
            """)
            sql = """
                SELECT category, COALESCE(index_group_name, '__all__') AS index_group_name,
                       age_band,
                       COUNT(DISTINCT customer_id)::BIGINT AS unique_customers,
                       MIN(transaction_date)::VARCHAR AS period_start,
                       MAX(transaction_date)::VARCHAR AS period_end
                FROM (
                  SELECT category, index_group_name, customer_id, transaction_date,
                         CASE WHEN age BETWEEN 16 AND 100 THEN
                         CASE WHEN age < 20 THEN 'under_20' WHEN age < 30 THEN '20_29'
                              WHEN age < 40 THEN '30_39' WHEN age < 50 THEN '40_49' ELSE '50_plus' END
                         ELSE 'unknown_age' END AS age_band
                    FROM (
                      SELECT a.category, a.index_group_name, t.customer_id, c.age,
                             TRY_CAST(t.t_dat AS DATE) AS transaction_date
                      FROM hm_age_transactions AS t
                      INNER JOIN hm_age_articles AS a USING (article_id)
                      INNER JOIN hm_age_customers AS c USING (customer_id)
                      WHERE a.category IS NOT NULL AND t.customer_id IS NOT NULL
                    ) AS joined
                ) AS categorized
                GROUP BY GROUPING SETS ((category, index_group_name, age_band), (category, age_band))
            """
            rows = conn.execute(sql).fetchall()
            _LOGGER.info("H&M age SQL scan and aggregate completed in %.3fs output_rows=%d",
                         time.perf_counter() - query_started, len(rows))
            aggregate_map: dict[tuple[str, str], dict[str, Any]] = {}
            categories = sorted({str(row[0]) for row in rows})
            genders = (("women", "Ladieswear", "women"), ("men", "Menswear", "men"), ("", "__all__", None))
            for category in categories:
                for gender_key, _segment, gender in genders:
                    profile = _build_profiles(
                        rows, requested_category=category, gender=gender,
                        mapping_id=mapping["mapping_id"], mapping_sha256=_mapping_sha256(mapping),
                        source_refs=tuple({"object_key": key, "version": version}
                                          for key, version in zip(keys, versions, strict=True)),
                    )
                    if profile is not None:
                        aggregate_map[(category.casefold(), gender_key)] = profile
            # Planning draft category names may be broader than H&M's
            # mapped merchandise labels. Add exact H&M product-type aliases to
            # the same aggregate without counting customers twice.
            for product_type, mapped_category in mapping["product_type_name"].items():
                source_rows = [row for row in rows if str(row[0]).strip().casefold() == mapped_category.casefold()]
                if not source_rows:
                    continue
                for gender_key, _segment, gender in genders:
                    profile = _build_profiles(
                        rows, requested_category=mapped_category, gender=gender,
                        mapping_id=mapping["mapping_id"], mapping_sha256=_mapping_sha256(mapping),
                        source_refs=tuple({"object_key": key, "version": version}
                                          for key, version in zip(keys, versions, strict=True)),
                    )
                    if profile is not None:
                        aggregate_map[(product_type.casefold(), gender_key)] = {
                            **profile, "requested_product_type_context": product_type,
                        }
            return aggregate_map
        finally:
            conn.close()
