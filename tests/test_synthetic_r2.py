from __future__ import annotations

import hashlib
import io
import json
import unittest
from pathlib import Path

from market_research_agent.sources.market.synthetic_r2 import (
    SyntheticR2BundleError,
    SyntheticR2FixtureBundleStore,
)
from market_research_agent.sources.shared.r2_object_store import R2ObjectStore, R2Settings
from market_research_agent.synthetic_rag import SYNTHETIC_MARKERS

ROOT = Path(__file__).resolve().parents[1]


class FakeR2Client:
    def __init__(self, objects, metadata=None):
        self.objects = objects
        self.calls = []
        self.metadata = metadata if metadata is not None else {
            "synthetic": "true", "data_nature": "synthetic_test",
            "permission_status": "authorized_for_synthetic_testing_only",
            "operational_eligible": "false",
        }

    def get_object(self, *, Bucket, Key):
        self.calls.append(Key)
        value = self.objects[Key]
        return {"ContentLength": len(value), "Body": io.BytesIO(value), "Metadata": self.metadata}


class SyntheticR2BundleTests(unittest.TestCase):
    def make_release(self, *, corrupt_hash=False, outside_key=False):
        release_root = "example/synthetic-release/manifest.json"
        groups = {
            "runtime/query_coverage": ROOT / "examples/query_coverage/generated",
            "runtime/market_scenario": ROOT / "examples/synthetic_market_scenario/generated",
        }
        objects = {}
        entries = []
        for group, directory in groups.items():
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(directory).as_posix()
                object_key = f"{release_root}/{group}/{relative}"
                body = path.read_bytes()
                digest = hashlib.sha256(body).hexdigest()
                if corrupt_hash and not entries:
                    digest = "0" * 64
                if outside_key and not entries:
                    object_key = "other-prefix/object.json"
                objects[object_key] = body
                entries.append({
                    "dataset_group": group, "relative_path": relative,
                    "object_key": object_key, "size_bytes": len(body), "sha256": digest,
                })
        # A release may also contain evaluation objects, but the service must not fetch them.
        entries.append({
            "dataset_group": "evaluation/synthetic_rag_analysis",
            "relative_path": "v1/cases.json", "object_key": f"{release_root}/evaluation/cases.json",
            "size_bytes": 100, "sha256": "a" * 64,
        })
        manifest = {
            "schema_version": "project07-synthetic-r2-bundle/1.0",
            "release_id": "test-release", "source_commit": "synthetic-test",
            **SYNTHETIC_MARKERS, "object_count": len(entries),
            "total_bytes": sum(item["size_bytes"] for item in entries), "objects": entries,
        }
        key = f"{release_root}/manifest.json"
        objects[key] = json.dumps(manifest).encode()
        return key, objects, entries

    def test_loads_verified_runtime_sources_and_never_reads_evaluation_objects(self):
        manifest_key, objects, entries = self.make_release()
        client = FakeR2Client(objects)
        store = SyntheticR2FixtureBundleStore(
            R2ObjectStore(R2Settings("account", "bucket", "key", "secret"), client), manifest_key,
        )
        bundle = store.load_bundle()
        self.assertGreater(len(bundle["observations"]["market_like"]), 0)
        self.assertGreater(len(bundle["observations"]["sales_like"]), 0)
        self.assertTrue(bundle["documents"])
        self.assertEqual(bundle["_r2_release"]["release_id"], "test-release")
        self.assertEqual(bundle["_r2_release"]["manifest_key"], manifest_key)
        self.assertTrue(bundle["_r2_release"]["manifest_sha256"])
        self.assertTrue(all("evaluation/" not in key for key in client.calls))
        self.assertEqual(len(client.calls), 1 + sum(e["dataset_group"].startswith("runtime/") for e in entries))

    def test_rejects_hash_mismatch_and_out_of_release_object_key(self):
        for corrupt_hash, outside_key in ((True, False), (False, True)):
            manifest_key, objects, _ = self.make_release(corrupt_hash=corrupt_hash, outside_key=outside_key)
            client = FakeR2Client(objects)
            store = SyntheticR2FixtureBundleStore(
                R2ObjectStore(R2Settings("account", "bucket", "key", "secret"), client), manifest_key,
            )
            if outside_key:
                with self.assertRaises(SyntheticR2BundleError):
                    store.load_bundle()
                self.assertEqual(len(client.calls), 1)
            else:
                with self.assertRaises(SyntheticR2BundleError):
                    store.load_bundle()

    def test_rejects_manifest_outside_project_release_namespace(self):
        with self.assertRaises(SyntheticR2BundleError):
            SyntheticR2FixtureBundleStore(
                R2ObjectStore(R2Settings("account", "bucket", "key", "secret"), FakeR2Client({})),
                "other-prefix/manifest.json",
            )

    def test_rejects_r2_object_without_safety_metadata(self):
        manifest_key, objects, _ = self.make_release()
        client = FakeR2Client(objects, metadata={})
        store = SyntheticR2FixtureBundleStore(
            R2ObjectStore(R2Settings("account", "bucket", "key", "secret"), client), manifest_key,
        )
        with self.assertRaises(SyntheticR2BundleError):
            store.load_bundle()
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
