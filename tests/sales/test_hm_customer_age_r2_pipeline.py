from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
import csv
import io
from unittest.mock import patch

from market_research_agent.sources.sales.hm_kaggle import hm_customer_age_source as age_module
from market_research_agent.sources.sales.hm_kaggle.duckdb_http import configure_httpfs
from market_research_agent.sources.sales.hm_kaggle.hm_research_aggregate import (
    HmResearchAggregateSource, _mapping_digest, _run_aggregate_query,
    build_snapshot_payload, serialize_snapshot, upload_snapshot,
)
from market_research_agent.sources.sales.hm_kaggle.hm_customer_age_source import (
    CustomerAgeSourceError,
    HmCustomerAgeResearchSource,
)
from market_research_agent.sources.market.fabric_price_r2_source import _REQUIRED_COLUMNS
from market_research_agent.sources.shared.r2_object_store import R2ObjectStore, R2Settings


class FakeR2Client:
    def __init__(self, failure: Exception | None = None, size: int = 42):
        self.failure = failure
        self.size = size
        self.keys: list[str] = []

    def head_object(self, *, Bucket: str, Key: str):
        self.keys.append(Key)
        if self.failure:
            raise self.failure
        return {"ContentLength": self.size, "VersionId": f"version-{Key}"}


class FakeDuckDB:
    @staticmethod
    def connect():
        raise AssertionError("the patched aggregate query should not open DuckDB")


class FakeConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)


class FakeUploadClient:
    def __init__(self, existing=None):
        self.existing = existing
        self.puts = []

    def head_object(self, **_kwargs):
        if self.existing is None:
            error = RuntimeError("missing object")
            error.response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}
            raise error
        return {"Metadata": self.existing}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


class FakeSnapshotClient:
    def __init__(self, payload):
        self.payload = payload
        self.get_count = 0
        self.head_count = 0

    def head_object(self, **_kwargs):
        self.head_count += 1
        return {"ContentLength": len(self.payload), "VersionId": "snapshot-v1"}

    def get_object(self, **_kwargs):
        self.get_count += 1
        import hashlib
        return {"ContentLength": len(self.payload), "Body": io.BytesIO(self.payload),
                "Metadata": {"sha256": hashlib.sha256(self.payload).hexdigest()}}


class AggregateSnapshotUploadTests(unittest.TestCase):
    def test_publish_writes_only_new_derived_content_and_is_idempotent(self):
        client = FakeUploadClient()

        class Store:
            @staticmethod
            def _get_client():
                return client

            settings = type("Settings", (), {"bucket": "synthetic-bucket"})()

        uploaded = upload_snapshot(
            Store(), key="derived/research-aggregate-snapshots/synthetic.json",
            payload=b'{"aggregate":true}', digest="digest", snapshot_id="snapshot",
        )
        self.assertTrue(uploaded)
        self.assertEqual(len(client.puts), 1)
        self.assertEqual(client.puts[0]["ContentType"], "application/json")
        self.assertEqual(client.puts[0]["Metadata"]["snapshot-id"], "snapshot")

    def test_existing_matching_snapshot_is_not_rewritten(self):
        client = FakeUploadClient({"sha256": "digest"})

        class Store:
            @staticmethod
            def _get_client():
                return client

            settings = type("Settings", (), {"bucket": "synthetic-bucket"})()

        uploaded = upload_snapshot(
            Store(), key="derived/research-aggregate-snapshots/synthetic.json",
            payload=b'{"aggregate":true}', digest="digest", snapshot_id="snapshot",
        )
        self.assertFalse(uploaded)
        self.assertEqual(client.puts, [])

    def test_runtime_reads_one_snapshot_once_and_uses_its_versioned_profile(self):
        from market_research_agent.sources.sales.hm_kaggle.hm_research_aggregate import (
            HmResearchAggregateSource, SCHEMA_VERSION, _mapping_digest,
        )
        from market_research_agent.sources.sales.hm_kaggle.hm_customer_age_source import _MAPPING_PATH

        mapping = json.loads(_MAPPING_PATH.read_text())
        age = {
            "category": "Outerwear", "unique_customer_denominator": 100,
            "target_age_cohort": {"age_band": "20_39"}, "sources": [],
        }
        price = {
            "category": "Outerwear", "merchandise_segment": "Ladieswear",
            "sample_count": 100, "article_count": 4,
            "period": {"start": "2020-01-01", "end": "2020-12-31"},
            "channel_codes": ["1"],
            "price_statistics": {"minimum": 0.01, "p25": 0.02, "median": 0.03,
                                 "p75": 0.04, "maximum": 0.05, "mean": 0.03,
                                 "currency": None, "unit": "normalized_source_price; currency not provided",
                                 "statistic": "transaction_price_distribution; unweighted transaction rows"},
            "sources": [], "category_mapping": {"mapping_id": mapping["mapping_id"],
                "mapping_version": mapping["mapping_version"], "mapping_fingerprint": _mapping_digest(mapping),
                "matching": "product_type_exact_then_product_group_fallback"},
        }
        fabric_csv = ",".join(sorted(_REQUIRED_COLUMNS)) + "\n"
        snapshot = {
            "schema_version": SCHEMA_VERSION, "snapshot_id": "a" * 64,
            "source_objects": [], "mapping_version": mapping["mapping_version"],
            "mapping_fingerprint": _mapping_digest(mapping),
            "fabric_source": {"object_key": "fabric.csv", "version": "fixture-v1",
                              "sha256": hashlib.sha256(fabric_csv.encode()).hexdigest()},
            "fabric_listing_csv": fabric_csv,
            "profiles": [{"category": "Outerwear", "segment": "ladieswear",
                          "customer_age_profile": age, "historical_price_aggregate": price}],
        }
        payload = json.dumps(snapshot).encode()
        client = FakeSnapshotClient(payload)
        store = R2ObjectStore(R2Settings("account", "bucket", "key-id", "secret"), client=client)
        from market_research_agent.sources.sales.hm_kaggle.hm_research_aggregate import output_key
        source = HmResearchAggregateSource(store=store, object_key=output_key(snapshot["snapshot_id"]))
        with patch.dict(os.environ, {"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true"}):
            first = source.analyze({"category": "Outerwear", "product_type": "Coat", "gender": "women"})
            second = source.analyze({"category": "Outerwear", "product_type": "Coat", "gender": "women"})
        self.assertEqual(first["customer_age_profile"]["unique_customer_denominator"], 100)
        self.assertEqual(first["historical_price"]["sample_count"], 100)
        self.assertEqual(second["historical_price"]["category"], "Outerwear")
        self.assertEqual(client.get_count, 1)
        self.assertEqual(client.head_count, 0)

    def test_runtime_rejects_retired_aggregate_snapshot_schema(self):
        from market_research_agent.sources.sales.hm_kaggle.hm_research_aggregate import (
            HmResearchAggregateSource,
        )

        retired = {
            "schema_version": "hm-sales-market-aggregate/1.0",
            "snapshot_id": "a" * 64,
            "source_objects": [],
            "profiles": [],
        }
        with self.assertRaisesRegex(ValueError, "schema is invalid"):
            HmResearchAggregateSource._validate_snapshot(retired)

    def test_current_snapshot_requires_bundled_fabric_listing(self):
        from market_research_agent.sources.sales.hm_kaggle.hm_research_aggregate import (
            SCHEMA_VERSION, HmResearchAggregateSource,
        )

        incomplete = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": "a" * 64,
            "source_objects": [],
            "profiles": [],
        }
        with self.assertRaisesRegex(ValueError, "schema is invalid"):
            HmResearchAggregateSource._validate_snapshot(incomplete)


class DuckDBHttpSettingsTests(unittest.TestCase):
    def test_httpfs_timeout_and_retry_defaults_are_bounded(self):
        connection = FakeConnection()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VOGUE_HM_HTTP_TIMEOUT_SECONDS", None)
            os.environ.pop("VOGUE_HM_HTTP_RETRIES", None)
            configure_httpfs(connection)
        self.assertEqual(connection.statements, ["SET http_timeout = 20", "SET http_retries = 1"])

    def test_invalid_httpfs_timeout_does_not_execute_sql(self):
        connection = FakeConnection()
        with patch.dict(os.environ, {"VOGUE_HM_HTTP_TIMEOUT_SECONDS": "2"}):
            with self.assertRaises(ValueError):
                configure_httpfs(connection)
        self.assertEqual(connection.statements, [])


class HmCustomerAgeR2PipelineTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true"})
        self.env_patch.start()
        age_module._CACHE.clear()
        age_module._CACHE_INFLIGHT.clear()

    def tearDown(self):
        self.env_patch.stop()
        age_module._CACHE.clear()
        age_module._CACHE_INFLIGHT.clear()

    def _source(self, client):
        from market_research_agent.sources.shared.r2_object_store import R2ObjectStore
        store = R2ObjectStore(
            R2Settings("account", "bucket", "key-id", "secret"), client=client,
        )
        manifest = json.loads((Path(__file__).resolve().parents[2] / "configs" / "r2-source-manifest.v1.json").read_text())
        return HmCustomerAgeResearchSource(
            store=store,
            customers_key=manifest["objects"]["hm_customers"],
            articles_key=manifest["objects"]["hm_articles"],
            transactions_key=manifest["objects"]["hm_transactions"],
        )

    def test_success_uses_head_versions_and_returns_aggregate_profile(self):
        client = FakeR2Client()
        rows = [
            ("Outerwear", "Ladieswear", "20_29", 12, "2019-01-01", "2020-01-01"),
            ("Outerwear", "Ladieswear", "30_39", 15, "2019-01-01", "2020-01-01"),
        ]
        profile = age_module._build_profiles(
            rows, requested_category="Outerwear", gender="women", mapping_id="map",
            mapping_version="v1", source_refs=(),
        )
        source = self._source(client)
        with patch.dict(sys.modules, {"duckdb": FakeDuckDB}), patch.object(
            HmCustomerAgeResearchSource, "_query_aggregates", return_value={("outerwear", "women"): profile},
        ) as query:
            result = source.profile("Outerwear", gender="women")
        self.assertEqual(result["unique_customer_denominator"], 27)
        self.assertEqual(len(client.keys), 3)
        self.assertEqual(query.call_count, 1)
        self.assertNotIn("customer_001", json.dumps(result))

    def test_korean_category_alias_selects_hm_knitwear_snapshot_profile(self):
        from market_research_agent.sources.sales.hm_kaggle.hm_customer_age_source import (
            _MAPPING_PATH, _mapped_requested_category,
        )
        mapping = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
        self.assertEqual(_mapped_requested_category("니트웨어", mapping), "Knitwear")
        snapshot = {
            "profiles": [{
                "category": "Knitwear", "segment": "__all__",
                "customer_age_profile": {"category": "Knitwear", "target_age_cohort": {"age_band": "20_39"}},
                "historical_price_aggregate": None,
            }],
        }
        selected = HmResearchAggregateSource._select(snapshot, {"category": "니트웨어", "material": "니트"})
        self.assertEqual(selected["customer_age_profile"]["category"], "Knitwear")
        self.assertIsNone(selected["historical_price"])
        self.assertEqual(selected["historical_price_source_status"], "no_match")

    def test_empty_aggregate_result_is_cached(self):
        source = self._source(FakeR2Client())
        with patch.dict(sys.modules, {"duckdb": FakeDuckDB}), patch.object(
            HmCustomerAgeResearchSource, "_query_aggregates", return_value={},
        ) as query:
            self.assertIsNone(source.profile("Unmapped", gender="women"))
            self.assertIsNone(source.profile("Unmapped", gender="women"))
        self.assertEqual(query.call_count, 1)

    def test_permission_denied_and_network_failures_remain_distinguishable(self):
        for failure, expected in (
            (PermissionError(), "AccessDenied"),
            (TimeoutError(), "NetworkTimeout"),
            (FileNotFoundError("No such key"), "ObjectNotFound"),
        ):
            with self.subTest(expected=expected), patch.dict(sys.modules, {"duckdb": FakeDuckDB}):
                source = self._source(FakeR2Client(failure))
                with self.assertRaisesRegex(CustomerAgeSourceError, expected):
                    source.profile("Outerwear", gender="women")

    def test_missing_namespace_key_is_classified_as_missing_object(self):
        error = FileNotFoundError("No such key")
        error.response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}
        source = self._source(FakeR2Client(error))
        with patch.dict(sys.modules, {"duckdb": FakeDuckDB}):
            with self.assertRaisesRegex(CustomerAgeSourceError, "stage=object_metadata; code=ObjectNotFound"):
                source.profile("Outerwear", gender="women")

    def test_r2_object_store_preserves_safe_provider_failure_category(self):
        class FailingReadClient:
            def get_object(self, **_kwargs):
                error = RuntimeError("private SDK detail with credentials")
                error.response = {
                    "Error": {"Code": "AccessDenied"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                }
                raise error

        from market_research_agent.sources.shared.r2_object_store import R2ObjectError
        store = R2ObjectStore(
            R2Settings("account", "bucket", "key-id", "secret"), client=FailingReadClient(),
        )
        with self.assertRaises(R2ObjectError) as raised:
            store.get_bytes_and_metadata("namespace/customers.csv")
        self.assertEqual(raised.exception.failure_code, "AccessDenied")
        self.assertNotIn("private SDK detail", str(raised.exception))

    def test_object_size_limit_is_enforced_before_query(self):
        client = FakeR2Client(size=300_000_001)
        source = self._source(client)
        with patch.dict(sys.modules, {"duckdb": FakeDuckDB}), patch.object(
            HmCustomerAgeResearchSource, "_query_aggregates",
        ) as query:
            with self.assertRaisesRegex(CustomerAgeSourceError, "size bound"):
                source.profile("Outerwear", gender="women")
        self.assertEqual(client.keys, ["customers.csv"])
        query.assert_not_called()

    def test_duckdb_schema_or_extension_errors_are_safe_and_diagnosable(self):
        scenarios = (
            (ValueError("Column age was not found in the CSV schema"), "InvalidFormatOrSchema"),
            (ValueError("No magic bytes found in Parquet file"), "InvalidFormatOrSchema"),
            (RuntimeError("httpfs extension not found"), "DuckDBExtensionUnavailable"),
            (RuntimeError("private query detail"), "RuntimeError"),
        )
        for failure, expected in scenarios:
            with self.subTest(expected=expected):
                client = FakeR2Client()
                source = self._source(client)
                with patch.dict(sys.modules, {"duckdb": FakeDuckDB}), patch.object(
                    HmCustomerAgeResearchSource, "_query_aggregates", side_effect=failure,
                ):
                    with self.assertRaisesRegex(CustomerAgeSourceError, expected) as raised:
                        source.profile("Outerwear", gender="women")
                self.assertNotIn("private query detail", str(raised.exception))
                self.assertIn("stage=duckdb_httpfs_and_aggregate", str(raised.exception))
                self.assertIn(f"code={expected}", str(raised.exception))
                self.assertEqual(len(client.keys), 3)

    def test_synthetic_csv_and_parquet_fixture_runs_the_real_age_query(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("optional DuckDB dependency is not installed")
        mapping = json.loads((Path(__file__).resolve().parents[2] / "configs" / "hm-category-map.v1.json").read_text())
        with tempfile.TemporaryDirectory(prefix="p07-hm-fixture-") as temp_dir:
            root = Path(temp_dir)
            articles = root / "articles.parquet"
            transactions = root / "transactions.parquet"
            customers = root / "customers.csv"
            connection = duckdb.connect()
            connection.execute(
                "CREATE TABLE a(article_id VARCHAR, index_group_name VARCHAR, product_type_name VARCHAR, product_group_name VARCHAR)"
            )
            connection.executemany("INSERT INTO a VALUES (?, ?, ?, ?)", [
                ("a1", "Ladieswear", "Coat", "Garment Upper body"),
                ("a2", "Ladieswear", "Coat", "Garment Upper body"),
            ])
            connection.execute(f"COPY a TO '{articles}' (FORMAT PARQUET)")
            connection.execute("CREATE TABLE t(customer_id VARCHAR, article_id VARCHAR, t_dat DATE, price DOUBLE, sales_channel_id INTEGER)")
            connection.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?)", [
                (f"synthetic-{index}", "a1" if index % 2 else "a2", "2020-01-01", (index + 1) / 1000, index % 2 + 1)
                for index in range(180)
            ])
            connection.execute(f"COPY t TO '{transactions}' (FORMAT PARQUET)")
            connection.close()
            with customers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["customer_id", "FN", "Active", "club_member_status", "fashion_news_frequency", "age", "postal_code"])
                for index in range(180):
                    writer.writerow([f"synthetic-{index}", "", "", "ACTIVE", "Regularly", 16 + index % 45, "postal"])

            paths = {"customers.csv": customers, "articles.parquet": articles, "transactions_train.parquet": transactions}
            real_connect = duckdb.connect
            query_statements = []

            class LocalObjectConnection:
                def __init__(self, connection):
                    self.connection = connection

                def execute(self, statement):
                    if statement.startswith(("CREATE SECRET", "LOAD httpfs", "SET http_timeout", "SET http_retries")):
                        return self
                    for key, path in paths.items():
                        statement = statement.replace(f"s3://synthetic-bucket/{key}", str(path))
                    if "GROUPING SETS" in statement:
                        query_statements.append(statement)
                    self.connection.execute(statement)
                    return self

                def executemany(self, statement, rows):
                    self.connection.executemany(statement, rows)
                    return self

                def fetchall(self):
                    return self.connection.fetchall()

                def close(self):
                    self.connection.close()

            class LocalDuckDB:
                @staticmethod
                def connect():
                    return LocalObjectConnection(real_connect())

            settings = R2Settings("account", "synthetic-bucket", "key-id", "secret")
            keys = ("customers.csv", "articles.parquet", "transactions_train.parquet")
            with patch.dict(sys.modules, {"duckdb": LocalDuckDB}), patch.dict(
                os.environ, {"VOGUE_DUCKDB_EXTENSION_DIRECTORY": ""}, clear=False,
            ):
                result = HmCustomerAgeResearchSource._query_aggregates(
                    LocalDuckDB, settings, keys, mapping, ("c1", "a1", "t1"),
                )
                age_query_count = len(query_statements)
                aggregate_rows = _run_aggregate_query(LocalDuckDB, settings, keys, mapping)
            self.assertEqual(age_query_count, 1)
            self.assertEqual(len(query_statements) - age_query_count, 1)
            profile = result[(mapping["product_type_name"]["Coat"].casefold(), "women")]
            self.assertEqual(profile["unique_customer_denominator"], 180)
            self.assertEqual(profile["target_age_cohort"]["age_band"], "40_plus")
            self.assertEqual(profile["sources"][0]["object_key"], "customers.csv")
            self.assertNotIn("synthetic-0", json.dumps(profile))
            refs = tuple({"object_key": key, "version": version} for key, version in zip(keys, ("c1", "a1", "t1"), strict=True))
            snapshot = build_snapshot_payload(
                aggregate_rows, source_refs=refs, mapping=mapping,
                mapping_fingerprint=_mapping_digest(mapping),
            )
            payload, _digest = serialize_snapshot(snapshot)
            stored_profile = next(
                row for row in snapshot["profiles"]
                if row["category"].casefold() == mapping["product_type_name"]["Coat"].casefold()
                and row["segment"] == "ladieswear"
            )
            self.assertEqual(stored_profile["customer_age_profile"]["unique_customer_denominator"], 180)
            self.assertEqual(stored_profile["historical_price_aggregate"]["sample_count"], 180)
            self.assertNotIn(b"synthetic-0", payload)
            runtime_result = HmResearchAggregateSource._select(
                snapshot, {"category": "Outerwear", "product_type": "Coat", "gender": "women", "season": "winter"},
            )
            self.assertEqual(runtime_result["customer_age_profile"]["unique_customer_denominator"], 180)
            self.assertEqual(runtime_result["historical_price"]["sample_count"], 180)
            self.assertEqual(runtime_result["historical_price"]["category"], "Outerwear")


if __name__ == "__main__":
    unittest.main()
