from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from market_research_agent.domain.price_references import analyze_fabric_snapshot


class PriceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_env = {name: os.environ.get(name) for name in (
            "FABRIC_PRICE_SNAPSHOT_PATH")}
        self.addCleanup(self.restore_env)

    def restore_env(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def write(self, name: str, content: str) -> str:
        path = Path(self.temp.name) / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_fabric_keeps_grade_and_original_and_normalized_unit(self):
        os.environ["FABRIC_PRICE_SNAPSHOT_PATH"] = self.write(
            "fabric.csv",
            "material,composition,construction,weight_gsm,width,color,source_grade,price,currency,unit,source_url,collected_at,permission_status\n"
            "wool blend,70% wool,plain,280,150cm,navy,public_sale,9,KRW,yd,https://example.test/f,2026-10-01T00:00:00Z,authorized\n"
        )
        result = analyze_fabric_snapshot({"material": "Wool Blend"})
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row["source_grade"], "public_sale")
        self.assertEqual(row["original_price_stats"]["median"], 9)
        self.assertEqual(row["normalized_unit"], "per_m")
        self.assertAlmostEqual(row["normalized_price_stats"]["median"], 9 / 0.9144)
        self.assertIn("not a confirmed production purchase cost", row["limitations"][0])


if __name__ == "__main__":
    unittest.main()
