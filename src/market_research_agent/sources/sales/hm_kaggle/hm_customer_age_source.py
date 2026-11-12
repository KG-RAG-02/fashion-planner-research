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
import threading
from typing import Any
from urllib.parse import urlsplit

from ....sources.shared.r2_object_store import R2ObjectStore, R2Settings


class CustomerAgeSourceError(RuntimeError):
    """Safe, non-sensitive age-profile source error."""


_ROOT = Path(__file__).resolve().parents[5]
_MAPPING_PATH = _ROOT / "configs" / "hm-category-map.v1.json"
_CACHE: dict[tuple[str, ...], dict[tuple[str, str], dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_MIN_CELL_SIZE = 10
_CSV_COLUMNS = (
    "'customer_id': 'VARCHAR', 'FN': 'DOUBLE', 'Active': 'DOUBLE', "
    "'club_member_status': 'VARCHAR', 'fashion_news_frequency': 'VARCHAR', "
    "'age': 'INTEGER', 'postal_code': 'VARCHAR'"
)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _checked_key(value: str, default: str) -> str:
    key = value.strip() or default
    if (key.startswith("/") or ".." in key.split("/")
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}", key) is None):
        raise CustomerAgeSourceError("An H&M research object key is invalid.")
    return key


def _age_band(age: int | None) -> str:
    if age is None or age < 16 or age > 100:
        return "unknown_age"
    if age < 20:
        return "under_20"
    if age < 30:
        return "20_29"
    if age < 40:
        return "30_39"
    if age < 50:
        return "40_49"
    return "50_plus"


def _build_profiles(
    rows: list[tuple[Any, ...]], *, requested_category: str, gender: str | None,
    mapping_id: str, mapping_version: str, source_refs: tuple[dict[str, str], ...],
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
        "category_mapping": {"mapping_id": mapping_id, "mapping_version": mapping_version,
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
            "Historical H&M Kaggle competition data; research-only and not current VOGUE customer behavior or demand.",
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

    def profile(self, category: str, *, gender: str | None = None) -> dict[str, Any] | None:
        if os.getenv("VOGUE_HM_RESEARCH_NONCOMMERCIAL", "").strip().casefold() not in {"1", "true", "yes"}:
            raise CustomerAgeSourceError(
                "Set VOGUE_HM_RESEARCH_NONCOMMERCIAL=true only for non-commercial research use."
            )
        if not isinstance(category, str) or not category.strip():
            raise CustomerAgeSourceError("A product category is required for age-band analysis.")
        try:
            import duckdb
        except ImportError:
            raise CustomerAgeSourceError("H&M age research requires the optional duckdb dependency.") from None
        try:
            store = self.store or R2ObjectStore.from_env()
        except Exception:
            raise CustomerAgeSourceError("R2 research access is not configured safely.") from None
        customers_key = _checked_key(self.customers_key or os.getenv("PROJECT07_HM_CUSTOMERS_R2_KEY", ""), "customers.csv")
        articles_key = _checked_key(self.articles_key or os.getenv("PROJECT07_HM_ARTICLES_R2_KEY", ""), "articles.parquet")
        transactions_key = _checked_key(self.transactions_key or os.getenv("PROJECT07_HM_TRANSACTIONS_R2_KEY", ""), "transactions_train.parquet")
        keys = (customers_key, articles_key, transactions_key)
        try:
            client = store._get_client()
            versions = []
            max_sizes = {customers_key: 300_000_000, articles_key: 50_000_000, transactions_key: 1_000_000_000}
            for key in keys:
                head = client.head_object(Bucket=store.settings.bucket, Key=key)
                size = int(head.get("ContentLength", 0))
                if size <= 0 or size > max_sizes[key]:
                    raise CustomerAgeSourceError("An H&M research object exceeds its allowed size bound.")
                versions.append(str(head.get("VersionId") or head.get("ETag", "")).strip('"'))
            mapping = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
            mapping_digest = hashlib.sha256(
                json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            cache_key = tuple((store.settings.bucket, *keys, *versions, mapping["mapping_version"], mapping_digest))
            with _CACHE_LOCK:
                aggregate_map = _CACHE.get(cache_key)
                if aggregate_map is None:
                    aggregate_map = self._query_aggregates(duckdb, store.settings, keys, mapping, tuple(versions))
                    _CACHE[cache_key] = aggregate_map
            gender_key = "" if (gender or "").casefold() in {"", "unknown", "unisex"} else gender.casefold()
            profile = aggregate_map.get((category.strip().casefold(), gender_key))
            if profile is None:
                return None
            return profile
        except CustomerAgeSourceError:
            raise
        except Exception:
            raise CustomerAgeSourceError("H&M research aggregates could not be read safely from R2.") from None

    @staticmethod
    def _query_aggregates(duckdb: Any, settings: R2Settings, keys: tuple[str, ...], mapping: dict[str, Any], versions: tuple[str, ...]) -> dict[tuple[str, str], dict[str, Any]]:
        conn = duckdb.connect()
        try:
            extension_dir = os.getenv("VOGUE_DUCKDB_EXTENSION_DIRECTORY", "").strip()
            if extension_dir:
                conn.execute("SET extension_directory = " + _sql_string(extension_dir))
            try:
                conn.execute("LOAD httpfs")
            except Exception:
                raise CustomerAgeSourceError(
                    "DuckDB httpfs must be installed in the runtime before R2 age research can run."
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
            sql = f"""
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
                ), customers AS (
                    SELECT customer_id, age FROM read_csv(
                        {customers_uri}, header=true, delim=',', columns={customer_schema}, sample_size=1000
                    )
                ), joined AS (
                    SELECT a.category, a.index_group_name, t.customer_id, c.age,
                           TRY_CAST(t.t_dat AS DATE) AS transaction_date
                    FROM read_parquet({transactions_uri}) AS t
                    INNER JOIN mapped_articles AS a USING (article_id)
                    INNER JOIN customers AS c USING (customer_id)
                    WHERE a.category IS NOT NULL
                )
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
                  FROM joined
                ) AS categorized
                GROUP BY GROUPING SETS ((category, index_group_name, age_band), (category, age_band))
            """
            rows = conn.execute(sql).fetchall()
            aggregate_map: dict[tuple[str, str], dict[str, Any]] = {}
            categories = sorted({str(row[0]) for row in rows})
            genders = (("women", "Ladieswear", "women"), ("men", "Menswear", "men"), ("", "__all__", None))
            for category in categories:
                for gender_key, _segment, gender in genders:
                    profile = _build_profiles(
                        rows, requested_category=category, gender=gender,
                        mapping_id=mapping["mapping_id"], mapping_version=mapping["mapping_version"],
                        source_refs=tuple({"object_key": key, "version": version}
                                          for key, version in zip(keys, versions, strict=True)),
                    )
                    if profile is not None:
                        aggregate_map[(category.casefold(), gender_key)] = profile
            return aggregate_map
        finally:
            conn.close()
