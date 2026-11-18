from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fashion_planner_research.sources.shared.r2_source_manifest import r2_source_key


class R2SourceManifestTests(unittest.TestCase):
    def test_manifest_value_selects_fabric_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({
                "schema_version": "project07-r2-source-manifest/1.0",
                "objects": {"fabric_prices": "synthetic/from-manifest.csv"},
            }), encoding="utf-8")
            with patch.dict(os.environ, {
                "PROJECT07_R2_SOURCE_MANIFEST": str(path),
            }, clear=True):
                result = r2_source_key(
                    "fabric_prices", default="synthetic/default.csv",
                )
        self.assertEqual(result, "synthetic/from-manifest.csv")

    def test_missing_explicit_manifest_is_not_silently_replaced_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "does-not-exist.json")
            with patch.dict(os.environ, {
                "PROJECT07_R2_SOURCE_MANIFEST": missing,
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "manifest does not exist"):
                    r2_source_key(
                        "fabric_prices", default="synthetic/default.csv",
                    )

    def test_empty_explicit_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"PROJECT07_R2_SOURCE_MANIFEST": str(path)}, clear=True):
                with self.assertRaisesRegex(ValueError, "unsupported schema"):
                    r2_source_key("fabric_prices", default="synthetic/default.csv")


if __name__ == "__main__":
    unittest.main()
