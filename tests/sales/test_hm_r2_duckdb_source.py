from __future__ import annotations

import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_research_agent.domain.models import MarketResearchContext
from market_research_agent.sources.sales.hm_kaggle.hm_r2_duckdb_source import (
    R2DuckDBHmHistoricalSalesSource,
)
from market_research_agent.sources.shared.r2_object_store import R2ObjectStore, R2Settings


HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None


class FakeR2Client:
    def __init__(self, payload: bytes, *, head_version="v1", get_version="v1", head_size=None):
        self.payload = payload
        self.head_version = head_version
        self.get_version = get_version
        self.head_size = len(payload) if head_size is None else head_size
        self.calls = []
        self.etag = hashlib.md5(payload).hexdigest()  # nosec: opaque test fixture version only

    def head_object(self, *, Bucket, Key):
        self.calls.append(("head", Key))
        return {"ContentLength": self.head_size, "VersionId": self.head_version, "ETag": self.etag}

    def get_object(self, *, Bucket, Key):
        self.calls.append(("get", Key))
        return {"ContentLength": len(self.payload), "VersionId": self.get_version,
                "ETag": self.etag, "Body": io.BytesIO(self.payload)}


@unittest.skipUnless(HAS_DUCKDB, "duckdb optional dependency is required")
class HmR2DuckDBTests(unittest.TestCase):
    def make_database(self, path: Path, *, include_hm_schema=True, bad_hm_schema=False) -> bytes:
        import duckdb

        con = duckdb.connect(str(path))
        try:
            if include_hm_schema and not bad_hm_schema:
                con.execute("""
                    CREATE TABLE articles (
                        article_id VARCHAR, product_group_name VARCHAR, product_type_name VARCHAR
                    )
                """)
                con.execute("""
                    INSERT INTO articles VALUES
                      ('SYN-A1', 'Garment Upper body', 'Sweater'),
                      ('SYN-A2', 'Garment Upper body', 'Sweater'),
                      ('SYN-X1', 'Shoes', 'Sneakers')
                """)
                con.execute("""
                    CREATE TABLE transactions (
                        t_dat VARCHAR, customer_id VARCHAR, article_id VARCHAR,
                        price DOUBLE, sales_channel_id INTEGER
                    )
                """)
                con.execute("""
                    INSERT INTO transactions VALUES
                      ('2020-09-21', 'SYN-CUSTOMER-SECRET', 'SYN-A1', 0.1, 1),
                      ('2020-09-22', 'SYN-CUSTOMER-SECRET', 'SYN-A2', 0.2, 1),
                      ('2020-09-22', 'SYN-CUSTOMER-SECRET', 'SYN-A1', 0.3, 2),
                      ('2020-09-28', 'SYN-CUSTOMER-SECRET', 'SYN-A1', 0.4, 2),
                      ('2020-09-22', 'SYN-CUSTOMER-SECRET', 'SYN-X1', 0.5, 1)
                """)
                # Intentionally absent: the H&M source must not depend on customers.
            elif bad_hm_schema:
                con.execute("CREATE TABLE articles (article_id VARCHAR, product_group_name VARCHAR)")
                con.execute("CREATE TABLE transactions (t_dat VARCHAR, article_id VARCHAR, sales_channel_id INTEGER)")
            else:
                con.execute("CREATE TABLE stock_v1 (article_id VARCHAR)")
        finally:
            con.close()
        return path.read_bytes()

    def make_source(self, payload: bytes, root: Path, *, authorized=True, object_key="shared.duckdb",
                    head_version="v1", get_version="v1", head_size=None):
        client = FakeR2Client(payload, head_version=head_version, get_version=get_version,
                              head_size=head_size)
        store = R2ObjectStore(R2Settings("account", "bucket", "key", "secret"), client)
        source = R2DuckDBHmHistoricalSalesSource(
            {"Sweater": "Knitwear"}, "synthetic-mapping-v1", usage_authorized=authorized,
            store=store, object_key=object_key, cache_dir=root / "cache",
        )
        return source, client

    @staticmethod
    def context(category="Knitwear", requested_period="2020-09-27"):
        return MarketResearchContext(
            request_id="synthetic-r2-duckdb-test", planning_request={},
            requested_category=category, requested_metrics=("purchase_rows",),
            requested_period=requested_period, query_intent="sales_observation",
        )

    def test_usage_flag_is_ignored_but_mapping_gates_precede_any_r2_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.duckdb"
            payload = self.make_database(path)
            source, client = self.make_source(payload, Path(temporary), authorized=False)
            report = source.research(self.context())
            # The retired usage flag does not gate the read: R2 is read once and the
            # week of 2020-09-21 yields Sweater rows per channel (1: 2 rows, 2: 1 row).
            self.assertEqual(report.sales_data.status, "ok")
            self.assertEqual([(o.value, o.channel) for o in report.sales_data.observations], [(2, "1"), (1, "2")])
            self.assertNotIn("permission_not_confirmed", [item.reason for item in report.sales_data.missing_data])
            self.assertEqual([call[0] for call in client.calls], ["head", "get"])
            # The mapping gates below must not add any R2 request of their own.
            client.calls.clear()

            source_no_category = R2DuckDBHmHistoricalSalesSource(
                {}, "synthetic-mapping-v1", usage_authorized=True,
                store=source.cache.store, object_key="shared.duckdb", cache_dir=Path(temporary) / "cache2",
            )
            report = source_no_category.research(self.context())
            self.assertEqual(report.sales_data.missing_data[0].reason, "mapping_failed")
            self.assertEqual(client.calls, [])

            source_no_version = R2DuckDBHmHistoricalSalesSource(
                {"Sweater": "Knitwear"}, "", usage_authorized=True,
                store=source.cache.store, object_key="shared.duckdb", cache_dir=Path(temporary) / "cache3",
            )
            report = source_no_version.research(self.context())
            self.assertEqual(report.sales_data.status, "error")
            self.assertEqual(client.calls, [])

            source_wrong_target = R2DuckDBHmHistoricalSalesSource(
                {"Sweater": "Tops"}, "synthetic-mapping-v1", usage_authorized=True,
                store=source.cache.store, object_key="shared.duckdb", cache_dir=Path(temporary) / "cache4",
            )
            report = source_wrong_target.research(self.context())
            self.assertEqual(report.sales_data.missing_data[0].reason, "mapping_failed")
            self.assertEqual(client.calls, [])

    def test_read_only_query_aggregates_week_channel_and_preserves_lineage(self):
        import duckdb

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.make_database(root / "fixture.duckdb")
            source, client = self.make_source(payload, root)
            executed = []
            read_only_flags = []
            real_connect = duckdb.connect

            class ConnectionSpy:
                def __init__(self, connection):
                    self.connection = connection

                def execute(self, sql, parameters=None):
                    executed.append(sql)
                    self.assert_no_customer_access(sql)
                    return self.connection.execute(sql, parameters or [])

                @staticmethod
                def assert_no_customer_access(sql):
                    lowered = sql.casefold()
                    if "customers" in lowered or "customer_id" in lowered:
                        raise AssertionError("Customer data must not be queried.")

                def close(self):
                    self.connection.close()

            def connect_spy(*args, **kwargs):
                read_only_flags.append(kwargs.get("read_only"))
                return ConnectionSpy(real_connect(*args, **kwargs))

            with patch("duckdb.connect", side_effect=connect_spy):
                report = source.research(self.context())

            self.assertEqual(report.sales_data.status, "ok")
            observations = report.sales_data.observations
            self.assertEqual([(o.value, o.channel) for o in observations], [(2, "1"), (1, "2")])
            self.assertTrue(all(o.value > 0 for o in observations))
            self.assertTrue(all(o.unit == "purchase_rows" and o.period_grain == "week" for o in observations))
            self.assertTrue(all(o.period_start == "2020-09-21" and o.period_end == "2020-09-27" for o in observations))
            # Two weekly sales records plus ONE catalog record: both observations share the
            # same group/type taxonomy evidence, which the adapter emits once.
            self.assertEqual(len(report.evidence), 3)
            self.assertEqual({e.evidence_id for e in report.evidence},
                             {evidence_id for o in observations for evidence_id in o.evidence_ids})
            self.assertEqual(len({o.evidence_ids[1] for o in observations}), 1)
            self.assertTrue(all("r2://shared.duckdb#version=v1" in e.locator for e in report.evidence))
            self.assertTrue(all("transactions" in e.locator or "articles" in e.locator for e in report.evidence))
            self.assertNotIn("SYN-CUSTOMER-SECRET", repr(report))
            self.assertTrue(any("H&M 과거 데이터" in item for item in report.limitations))
            self.assertEqual(len([call for call in client.calls if call[0] == "get"]), 1)
            self.assertEqual(read_only_flags, [True])
            self.assertTrue(executed)

    def test_period_filters_by_week_overlap_and_no_data_stays_no_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.make_database(root / "fixture.duckdb")
            source, _client = self.make_source(payload, root)
            next_week = source.research(self.context(requested_period="2020-09-28"))
            self.assertEqual([(item.value, item.channel) for item in next_week.sales_data.observations], [(1, "2")])
            no_data = source.research(self.context(requested_period="2020-08"))
            self.assertEqual(no_data.sales_data.status, "no_results")
            self.assertEqual(no_data.sales_data.observations, ())

    def test_exact_type_mapping_precedes_broad_group_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.make_database(root / "fixture.duckdb")
            client = FakeR2Client(payload)
            store = R2ObjectStore(R2Settings("account", "bucket", "key", "secret"), client)
            source = R2DuckDBHmHistoricalSalesSource(
                {"Sweater": "Knitwear", "Garment Upper body": "Tops"},
                "synthetic-mapping-v1", usage_authorized=True,
                store=store, object_key="shared.duckdb", cache_dir=root / "cache",
            )
            report = source.research(self.context())
            # Every "Garment Upper body" article is a "Sweater", so the exact type mapping
            # wins for all of them and the broad "Tops" fallback never applies.
            tops = source.research(self.context(category="Tops"))
        self.assertEqual(report.sales_data.status, "ok")
        self.assertEqual([(item.value, item.channel) for item in report.sales_data.observations], [(2, "1"), (1, "2")])
        self.assertTrue(all(item.category == "Knitwear" for item in report.sales_data.observations))
        self.assertEqual(tops.sales_data.status, "no_results")
        self.assertEqual(tops.sales_data.observations, ())

    def test_r2_version_size_and_table_schema_errors_are_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.make_database(root / "fixture.duckdb")
            source, _client = self.make_source(payload, root, get_version="changed")
            report = source.research(self.context())
            self.assertEqual(report.sales_data.status, "error")
            self.assertIn("changed during download", " ".join(report.limitations))

            source, _client = self.make_source(payload, root / "size", head_size=len(payload) + 1)
            report = source.research(self.context())
            self.assertEqual(report.sales_data.status, "error")

            invalid_payload = self.make_database(root / "invalid.duckdb", include_hm_schema=False)
            source, _client = self.make_source(invalid_payload, root / "invalid")
            report = source.research(self.context())
            self.assertEqual(report.sales_data.status, "error")
            self.assertIn("missing required articles or transactions", " ".join(report.limitations))

            schema_payload = self.make_database(root / "bad-schema.duckdb", bad_hm_schema=True)
            source, _client = self.make_source(schema_payload, root / "bad-schema")
            report = source.research(self.context())
            self.assertEqual(report.sales_data.status, "error")
            self.assertIn("schema is unsupported", " ".join(report.limitations))

    def test_cache_uses_versioned_private_object_and_read_only_queries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.make_database(root / "fixture.duckdb")
            source, _client = self.make_source(payload, root)
            snapshot = source.cache.prepare()
            self.assertTrue(snapshot.path.is_file())
            self.assertEqual(snapshot.path.stat().st_size, len(payload))
            self.assertEqual(snapshot.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(snapshot.object_key, "shared.duckdb")
            self.assertEqual(snapshot.version, "v1")


if __name__ == "__main__":
    unittest.main()
