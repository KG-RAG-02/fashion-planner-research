"""Read H&M historical aggregates from the shared Project_06 R2 DuckDB."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ....domain.models import MarketResearchContext
from ....domain.sales import SalesDataResult, SalesObservation
from ....sources.shared.r2_object_store import R2ObjectStore
from .hm_sales_adapter import HmHistoricalSalesSource, HmSourceReadError, _period_bounds

_SOURCE_ID = "hm-kaggle-personalized-fashion-recommendations"
_MAX_DB_BYTES = 2_000_000_000
_CHUNK_BYTES = 8 * 1024 * 1024
_DEFAULT_CACHE_DIR = "data/local/r2_hm_db"


class HmDuckDBSourceError(HmSourceReadError):
    """A safe, user-facing R2 DuckDB source error."""


@dataclass(frozen=True)
class R2DatabaseSnapshot:
    path: Path
    object_key: str
    version: str
    etag: str
    size_bytes: int


class R2DuckDBCache:
    """Download one R2 DuckDB version into a private, atomic local cache."""

    def __init__(
        self,
        store: R2ObjectStore | None = None,
        *,
        object_key: str | None = None,
        cache_dir: Path | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.store = store
        self.object_key = (object_key or os.getenv("PROJECT07_HM_R2_DB_KEY", "")
                           or os.getenv("R2_DB_KEY", "")).strip()
        # A blank VOGUE_R2_HM_DB_CACHE_DIR keeps the default instead of the working directory.
        self.cache_dir = Path(cache_dir or os.getenv("VOGUE_R2_HM_DB_CACHE_DIR", "").strip() or _DEFAULT_CACHE_DIR)
        try:
            self.max_bytes = (max_bytes if max_bytes is not None
                              else int(os.getenv("VOGUE_R2_HM_DB_MAX_BYTES", str(_MAX_DB_BYTES))))
        except ValueError:
            raise HmDuckDBSourceError("The R2 DuckDB size limit configuration is invalid.") from None
        if self.max_bytes < 1:
            raise HmDuckDBSourceError("The R2 DuckDB size limit must be positive.")
        self._lock = threading.Lock()

    def prepare(self) -> R2DatabaseSnapshot:
        with self._lock:
            try:
                store = self.store or R2ObjectStore.from_env()
                if (not self.object_key or self.object_key.startswith("/")
                        or ".." in self.object_key.split("/")):
                    raise HmDuckDBSourceError("Configure a valid R2_DB_KEY for the shared DuckDB source.")
                client = store._get_client()
                head = client.head_object(Bucket=store.settings.bucket, Key=self.object_key)
                etag = str(head.get("ETag", "")).strip('"')
                version_id = str(head.get("VersionId", ""))
                size = int(head.get("ContentLength", 0))
                if not (etag or version_id) or size <= 0 or size > self.max_bytes:
                    raise HmDuckDBSourceError("The shared R2 DuckDB version or object size is invalid.")
                version = version_id or etag
                identity = hashlib.sha256(
                    f"{store.settings.bucket}/{self.object_key}/{version}".encode()
                ).hexdigest()
                target = self.cache_dir / f"hm-{identity}.duckdb"
                if target.is_file() and target.stat().st_size == size:
                    try:
                        target.chmod(0o600)
                    except OSError:
                        pass
                    return R2DatabaseSnapshot(target, self.object_key, version, etag, size)

                self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    self.cache_dir.chmod(0o700)
                except OSError:
                    pass
                descriptor, temp_name = tempfile.mkstemp(
                    prefix=target.name + ".", suffix=".part", dir=self.cache_dir,
                )
                downloaded = 0
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        response = client.get_object(Bucket=store.settings.bucket, Key=self.object_key)
                        response_version_id = str(response.get("VersionId", ""))
                        response_etag = str(response.get("ETag", "")).strip('"')
                        if version_id and response_version_id != version_id:
                            raise HmDuckDBSourceError("The shared R2 DuckDB changed during download.")
                        if not version_id and response_etag != etag:
                            raise HmDuckDBSourceError("The shared R2 DuckDB changed during download.")
                        response_size = response.get("ContentLength")
                        if response_size is not None and int(response_size) != size:
                            raise HmDuckDBSourceError("The shared R2 DuckDB response size is inconsistent.")
                        body = response["Body"]
                        try:
                            while True:
                                chunk = body.read(_CHUNK_BYTES)
                                if not chunk:
                                    break
                                downloaded += len(chunk)
                                if downloaded > self.max_bytes:
                                    raise HmDuckDBSourceError("The shared R2 DuckDB exceeds the configured size limit.")
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                        finally:
                            close = getattr(body, "close", None)
                            if callable(close):
                                close()
                    if downloaded != size:
                        raise HmDuckDBSourceError("The shared R2 DuckDB download size is inconsistent.")
                    os.chmod(temp_name, 0o600)
                    os.replace(temp_name, target)
                finally:
                    Path(temp_name).unlink(missing_ok=True)
                return R2DatabaseSnapshot(target, self.object_key, version, etag, size)
            except HmDuckDBSourceError:
                raise
            except Exception:
                # SDK exceptions may contain endpoint, key, or request data.
                raise HmDuckDBSourceError("The shared R2 DuckDB could not be read safely.") from None


class R2DuckDBHmHistoricalSalesSource(HmHistoricalSalesSource):
    """Use only H&M article and transaction tables; never query customer records."""

    def __init__(
        self,
        category_map,
        mapping_version: str,
        *,
        usage_authorized: bool,
        store: R2ObjectStore | None = None,
        object_key: str | None = None,
        cache_dir: Path | None = None,
        max_bytes: int | None = None,
    ) -> None:
        # The base path is not used: the permission and scope gates in research()
        # run before _load_sales_analysis() opens the R2 object.
        super().__init__(Path(".hm-r2-duckdb-not-materialized"), category_map, mapping_version,
                         usage_authorized=usage_authorized)
        self.cache = R2DuckDBCache(store, object_key=object_key, cache_dir=cache_dir, max_bytes=max_bytes)
        self._request_state = threading.local()
        self._period_cache: dict[str, tuple[str, str]] = {}
        self._period_lock = threading.Lock()

    @classmethod
    def from_env(cls, category_map, mapping_version: str, *, usage_authorized: bool):
        return cls(category_map, mapping_version, usage_authorized=usage_authorized)

    def _load_sales_analysis(self, context: MarketResearchContext, category: str):
        snapshot = self.cache.prepare()
        self._request_state.snapshot = snapshot
        try:
            import duckdb
        except ImportError:
            raise HmDuckDBSourceError("H&M R2 DuckDB reads require the optional duckdb dependency.") from None
        try:
            con = duckdb.connect(str(snapshot.path), read_only=True)
            try:
                sales, observation_period = self._aggregate(con, snapshot, context, category)
            finally:
                con.close()
        except HmDuckDBSourceError:
            raise
        except Exception:
            # Do not include SQL or data values in source errors.
            raise HmDuckDBSourceError("The shared R2 DuckDB has an invalid H&M schema or could not be queried.") from None
        manifest = {
            "manifest_id": f"r2-duckdb:{snapshot.object_key}:{snapshot.version}",
            "observation_period": observation_period,
            "source_id": _SOURCE_ID,
            "source_type": "project06-shared-duckdb",
        }
        return sales, manifest

    def _aggregate(self, con: Any, snapshot: R2DatabaseSnapshot,
                   context: MarketResearchContext, category: str) -> tuple[SalesDataResult, dict[str, str]]:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if not {"articles", "transactions"}.issubset(tables):
            raise HmDuckDBSourceError("The shared R2 DuckDB is missing required articles or transactions tables.")
        article_columns = {row[0] for row in con.execute('DESCRIBE "articles"').fetchall()}
        transaction_columns = {row[0] for row in con.execute('DESCRIBE "transactions"').fetchall()}
        required_articles = {"article_id", "product_group_name", "product_type_name"}
        required_transactions = {"t_dat", "article_id", "sales_channel_id"}
        if not required_articles.issubset(article_columns) or not required_transactions.issubset(transaction_columns):
            raise HmDuckDBSourceError("The shared R2 DuckDB H&M table schema is unsupported.")

        requested_target = category.strip().casefold()
        source_mappings = {
            key: value for key, value in self.category_map.items()
        }
        source_keys = sorted(source_mappings)
        targets = [value for value in source_mappings.values() if value.casefold() == requested_target]
        if not targets:
            return SalesDataResult(
                "no_results",
                limitations=("No configured H&M source category maps to the requested category.",),
            ), {"start": "", "end": ""}

        period_bounds = _period_bounds(context.requested_period)
        source_period = self._source_period(con, snapshot.version)
        source_start = date.fromisoformat(source_period[0])
        source_end = date.fromisoformat(source_period[1])
        type_cases = " ".join("WHEN ? THEN ?" for _ in source_keys)
        group_cases = " ".join("WHEN ? THEN ?" for _ in source_keys)
        mapped_params: list[Any] = []
        for key, value in source_mappings.items():
            mapped_params.extend((key, value))
        for key, value in source_mappings.items():
            mapped_params.extend((key, value))
        placeholders = ",".join("?" for _ in source_keys)
        sql = f"""
            WITH grouped AS (
                SELECT
                    a.product_group_name AS group_name,
                    a.product_type_name AS type_name,
                    LOWER(TRIM(COALESCE(a.product_group_name, ''))) AS group_key,
                    LOWER(TRIM(COALESCE(a.product_type_name, ''))) AS type_key,
                    DATE_TRUNC('week', TRY_CAST(t.t_dat AS DATE))::DATE AS week_start,
                    COALESCE(CAST(t.sales_channel_id AS VARCHAR), '(unknown)') AS channel_code,
                    COUNT(*)::BIGINT AS purchase_rows
                FROM transactions AS t
                INNER JOIN articles AS a ON a.article_id = t.article_id
                WHERE TRY_CAST(t.t_dat AS DATE) IS NOT NULL
                  AND (LOWER(TRIM(COALESCE(a.product_group_name, ''))) IN ({placeholders})
                       OR LOWER(TRIM(COALESCE(a.product_type_name, ''))) IN ({placeholders}))
                  {"AND DATE_TRUNC('week', TRY_CAST(t.t_dat AS DATE))::DATE <= CAST(? AS DATE)" if period_bounds else ""}
                  {"AND DATE_TRUNC('week', TRY_CAST(t.t_dat AS DATE))::DATE + INTERVAL '6 days' >= CAST(? AS DATE)" if period_bounds else ""}
                GROUP BY 1,2,3,4,5,6
            ), classified AS (
                SELECT *,
                    CASE type_key {type_cases} ELSE NULL END AS type_target,
                    CASE group_key {group_cases} ELSE NULL END AS group_target
                FROM grouped
            ), resolved AS (
                SELECT *,
                    FALSE AS mapping_conflict,
                    COALESCE(type_target, group_target) AS mapped_category
                FROM classified
            ), stats AS (
                SELECT COALESCE(SUM(purchase_rows) FILTER (WHERE mapping_conflict), 0)::BIGINT AS conflicting_rows
                FROM resolved
            )
            SELECT r.mapped_category, r.group_name, r.type_name, r.week_start, r.channel_code,
                   SUM(r.purchase_rows)::BIGINT AS purchase_rows, stats.conflicting_rows
            FROM stats
            LEFT JOIN resolved AS r
              ON r.mapped_category IS NOT NULL AND LOWER(r.mapped_category) = ?
            GROUP BY 1,2,3,4,5,7
            ORDER BY 1,2,3,4,5
        """
        params: list[Any] = source_keys + source_keys
        if period_bounds:
            params.extend((period_bounds[1].isoformat(), period_bounds[0].isoformat()))
        params.extend(mapped_params)
        params.append(requested_target)
        rows = con.execute(sql, params).fetchall()
        conflicting_rows = int(rows[0][6]) if rows else 0
        matched_rows = [row for row in rows if row[0] is not None]

        if not matched_rows:
            message = "No H&M transaction observations matched the mapped category and requested period."
            limitations = [message, "No matching weekly observation is represented as a numeric zero."]
            if conflicting_rows:
                limitations.append(f"{conflicting_rows} purchase rows with conflicting type/group category mappings were excluded.")
            return SalesDataResult("no_results", limitations=tuple(limitations)), {
                "start": source_start.isoformat(), "end": source_end.isoformat(),
            }

        source_fingerprint = hashlib.sha256(
            f"{snapshot.object_key}:{snapshot.version}".encode()
        ).hexdigest()
        observations: list[SalesObservation] = []
        for mapped, group_name, type_name, week_start_value, channel_code, purchase_rows, _conflicts in matched_rows:
            week_start = date.fromisoformat(str(week_start_value))
            week_end = date.fromordinal(week_start.toordinal() + 6)
            observed_start = max(source_start, week_start)
            observed_end = min(source_end, week_end)
            if period_bounds and (week_end < period_bounds[0] or week_start > period_bounds[1]):
                continue
            channel = None if channel_code == "(unknown)" else str(channel_code)
            identity = "|".join((str(mapped), str(group_name), str(type_name), week_start.isoformat(), channel_code))
            obs_digest = hashlib.sha256(identity.encode()).hexdigest()[:20]
            sales_evidence_id = f"E-HM-R2-SALES-{source_fingerprint[:12]}-{obs_digest}"
            catalog_identity = f"{group_name}|{type_name}"
            catalog_digest = hashlib.sha256(catalog_identity.encode()).hexdigest()[:16]
            catalog_evidence_id = f"E-HM-R2-CATALOG-{source_fingerprint[:12]}-{catalog_digest}"
            observations.append(SalesObservation(
                observation_id=sales_evidence_id,
                metric_name="purchase_rows",
                value=int(purchase_rows),
                unit="purchase_rows",
                evidence_ids=(sales_evidence_id, catalog_evidence_id),
                category=str(mapped),
                period_start=observed_start.isoformat(),
                period_end=observed_end.isoformat(),
                period_grain="week",
                channel=channel,
                metric_definition=(
                    "Count of H&M historical transaction rows aggregated by week, article taxonomy, "
                    "and original sales_channel_id; not a customer count or demand estimate."
                ),
                product_mapping=self.mapping_version,
            ))
        if not observations:
            return SalesDataResult("no_results", limitations=("No matching weekly H&M observations were returned.",)), {
                "start": source_start.isoformat(), "end": source_end.isoformat(),
            }
        limitations = [
            "H&M 과거 데이터이며 현재 판매, 수요, 매출액, 시장규모 또는 시장점유율을 뜻하지 않습니다.",
            "purchase_rows counts historical transaction rows, not unique customers or units sold.",
            "The original sales_channel_id is preserved and is not interpreted as online or offline.",
            "Versioned H&M mapping is applied at product_type_name first, with product_group_name as a fallback when the product type is unmapped.",
            "Transactions without a matching article catalog record and rows with invalid dates are excluded; their counts are not measured by this query.",
            "A requested partial week returns the full purchase_rows aggregate for each overlapping source week.",
            "Missing weeks are not filled with zero. No transaction row for a week is not an observed zero.",
            f"R2 DuckDB object version: {snapshot.version}; source tables: articles and transactions.",
        ]
        if conflicting_rows:
            limitations.append(f"{conflicting_rows} purchase rows with conflicting type/group category mappings were excluded.")
        return SalesDataResult("partial" if conflicting_rows else "ok", observations=tuple(observations), limitations=tuple(limitations)), {
            "start": source_start.isoformat(), "end": source_end.isoformat(),
        }

    def _source_period(self, con: Any, version: str) -> tuple[str, str]:
        with self._period_lock:
            cached = self._period_cache.get(version)
            if cached:
                return cached
            row = con.execute(
                "SELECT MIN(TRY_CAST(t_dat AS DATE)), MAX(TRY_CAST(t_dat AS DATE)) FROM transactions"
            ).fetchone()
            if not row or row[0] is None or row[1] is None:
                raise HmDuckDBSourceError("The H&M transactions table has no valid observation dates.")
            value = (row[0].isoformat(), row[1].isoformat())
            self._period_cache[version] = value
            return value

    def _evidence_locator(self, source_type: str) -> str:
        snapshot = getattr(self._request_state, "snapshot", None)
        if snapshot is None:
            return super()._evidence_locator(source_type)
        table = "transactions" if source_type == "historical_sales" else "articles"
        return (
            f"r2://{snapshot.object_key}#version={snapshot.version}"
            f"&table={table}&aggregation=weekly_article_channel"
        )
