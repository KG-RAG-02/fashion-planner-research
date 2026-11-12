from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from market_research_agent.domain.price_references import analyze_fabric_snapshot, analyze_musinsa_snapshot


class PriceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_env = {name: os.environ.get(name) for name in (
            "MUSINSA_PRICE_SNAPSHOT_PATH", "FABRIC_PRICE_SNAPSHOT_PATH")}
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

    def test_musinsa_exact_mapping_authorized_rows_and_price_stats(self):
        os.environ["MUSINSA_PRICE_SNAPSHOT_PATH"] = self.write(
            "musinsa.csv",
            "project_category,gender,product_type,product_name,brand,product_id,source_url,regular_price,current_price,currency,collected_at,permission_status\n"
            "Outerwear,women,Jacket,Coat A,Brand A,1,https://example.test/1,100,80,KRW,2026-10-01T00:00:00Z,authorized\n"
            "Outerwear,women,Jacket,Coat B,Brand B,2,https://example.test/2,200,160,KRW,2026-10-02T00:00:00Z,authorized\n"
            "Outerwear,women,Jacket,Coat C,Brand C,3,https://example.test/3,999,1,KRW,2026-10-02T00:00:00Z,unknown\n"
            "Tops,women,Jacket,Other,Brand D,4,https://example.test/4,5,4,KRW,2026-10-02T00:00:00Z,authorized\n"
        )
        output = analyze_musinsa_snapshot({"category": "Outerwear", "gender": "women", "product_type": "Jacket"})
        self.assertEqual(output["sample_count"], 2)
        self.assertEqual(output["regular_price_stats"]["median"], 150)
        self.assertEqual(output["current_price_stats"]["median"], 120)
        self.assertTrue(all(x["source_url"] and x["collected_at"] for x in output["comparables"]))

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
