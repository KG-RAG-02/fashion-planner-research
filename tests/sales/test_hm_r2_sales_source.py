from __future__ import annotations

import hashlib
import json
import unittest

from market_research_agent.domain.models import MarketResearchContext
from market_research_agent.sources.sales.hm_kaggle.hm_r2_sales_source import R2HmHistoricalSalesSource


class FakeR2Store:
    def __init__(self, files=None, bundle=None):
        self.files = files or {}
        self.bundle = bundle
        self.calls = []

    def get_bytes(self, key, *, max_bytes):
        self.calls.append(("bytes", key))
        if key.endswith("/bundle.json"):
            return json.dumps(self.bundle).encode()
        return self.files[key]


def context(category="Outerwear"):
    return MarketResearchContext(
        request_id="synthetic-hm-r2-test", planning_request={},
        requested_category=category, requested_metrics=("purchase_rows",),
        query_intent="sales_observation",
    )


class HmR2SourceTests(unittest.TestCase):
    def make_source(self, *, authorized, hash_mismatch=False):
        processed = {
            "manifest.json": json.dumps({
                "manifest_id": "synthetic-test-manifest",
                "observation_period": {"start": "2018-01-01", "end": "2018-01-07"},
                "source_transactions": {"container_sha256": "synthetic-sales-hash"},
                "source_articles": {"container_sha256": "synthetic-catalog-hash"},
            }).encode(),
            "article_catalog.csv": (
                "article_id,product_code,product_group_name,product_type_name,colour_group_name,department_name\n"
                "SYN-A1,SYN-P1,Garment Upper body,Jacket,Black,Ladieswear\n"
            ).encode(),
            "weekly_sales_by_article_channel.csv": (
                "article_id,purchase_rows,week_start,sales_channel_id\n"
                "SYN-A1,3,2018-01-01,1\n"
            ).encode(),
        }
        prefix = "example/hm"
        entries = []
        for name, content in processed.items():
            digest = hashlib.sha256(content).hexdigest()
            entries.append({"name": name, "object_key": f"{prefix}/{name}",
                            "size_bytes": len(content), "sha256": "0" * 64 if hash_mismatch else digest})
        bundle = {
            "schema_version": "project07-hm-r2-processed-bundle/1.0",
            "source_id": "hm-kaggle-personalized-fashion-recommendations",
            "data_nature": "hm_kaggle_historical", "files": entries,
            "object_count": len(entries), "total_bytes": sum(item["size_bytes"] for item in entries),
        }
        store = FakeR2Store({f"{prefix}/{name}": body for name, body in processed.items()}, bundle)
        source = R2HmHistoricalSalesSource(
            {"Jacket": "Outerwear"}, "synthetic-map-v1", usage_authorized=authorized,
            store=store, manifest_key=f"{prefix}/bundle.json", allowed_prefix=prefix,
        )
        return source, store

    def test_permission_gate_prevents_even_r2_manifest_read(self):
        source, store = self.make_source(authorized=False)
        report = source.research(context())
        self.assertEqual(report.sales_data.missing_data[0].reason, "permission_not_confirmed")
        self.assertEqual(store.calls, [])

    def test_authorized_query_reads_only_manifest_allowlisted_processed_files(self):
        source, store = self.make_source(authorized=True)
        report = source.research(context())
        self.assertEqual(report.sales_data.status, "ok")
        self.assertEqual(report.sales_data.observations[0].value, 3)
        self.assertEqual(report.sales_data.observations[0].unit, "purchase_rows")
        self.assertTrue(all(item.permission_status.value == "authorized" for item in report.evidence))
        self.assertTrue(any("H&M 과거 데이터" in limitation for limitation in report.limitations))
        self.assertTrue(all(item.locator.startswith("r2://") and "sha256=" in item.locator for item in report.evidence))
        self.assertEqual(len(store.calls), 4)

    def test_category_and_hash_failures_are_safe(self):
        source, store = self.make_source(authorized=True)
        report = source.research(context(category=None))
        self.assertEqual(report.sales_data.missing_data[0].reason, "mapping_failed")
        self.assertEqual(store.calls, [])

        source, store = self.make_source(authorized=True, hash_mismatch=True)
        report = source.research(context())
        self.assertEqual(report.status.value, "error")
        self.assertNotIn("synthetic-sales-hash", " ".join(report.limitations))


if __name__ == "__main__":
    unittest.main()
