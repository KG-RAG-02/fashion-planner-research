from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from market_research_agent.domain.sales import SalesDataResult, SalesObservation, peer_percentile_scores
from market_research_agent.sources.hm_historical.hm_sales_adapter import load_hm_sales_analysis


class SalesDomainTests(unittest.TestCase):
    def test_sales_result_distinguishes_zero_no_results_and_failure(self):
        zero = SalesObservation("O1", "units_sold", 0, "units", ("E1",), category="shorts")
        self.assertEqual(SalesDataResult("ok", (zero,)).observations[0].value, 0)
        self.assertEqual(SalesDataResult("no_results").status, "no_results")
        self.assertEqual(SalesDataResult("error", errors=("timeout",)).status, "error")

    def test_percentile_midrank_ties_and_missing(self):
        self.assertEqual(peer_percentile_scores({"a": 5, "b": 5, "c": 9, "d": None}),
                         {"a": 25.0, "b": 25.0, "c": 100.0, "d": None})


class HmSalesAdapterTests(unittest.TestCase):
    def _fixture(self, root: Path):
        (root / "manifest.json").write_text(json.dumps({
            "observation_period": {"start": "2018-01-01", "end": "2018-01-07"},
            "source_transactions": {"container_sha256": "saleshash"},
            "source_articles": {"container_sha256": "cataloghash"},
        }), encoding="utf-8")
        with (root / "article_catalog.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["article_id", "product_code", "product_group_name", "product_type_name", "colour_group_name", "department_name"])
            writer.writeheader()
            writer.writerow({"article_id":"A1", "product_code":"P1", "product_group_name":"Garment Lower body", "product_type_name":"Shorts", "colour_group_name":"Black", "department_name":"Menswear"})
            writer.writerow({"article_id":"A2", "product_code":"P1", "product_group_name":"Garment Lower body", "product_type_name":"Trousers", "colour_group_name":"Blue", "department_name":"Menswear"})
        with (root / "weekly_sales_by_article_channel.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["article_id", "purchase_rows", "week_start", "sales_channel_id"])
            writer.writeheader()
            writer.writerow({"article_id":"A1", "purchase_rows":3, "week_start":"2018-01-01", "sales_channel_id":"1"})
            writer.writerow({"article_id":"A2", "purchase_rows":8, "week_start":"2018-01-01", "sales_channel_id":"1"})
            writer.writerow({"article_id":"MISSING", "purchase_rows":9, "week_start":"2018-01-01", "sales_channel_id":"1"})

    def test_article_level_mapping_and_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._fixture(root)
            result = load_hm_sales_analysis(request_id="r1", schema_version="1.0",
                requested_category="shorts", source_category_map={"Shorts":"shorts"},
                category_mapping_version="test-map", processed_dir=root)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.observations), 1)
        observation = result.observations[0]
        self.assertEqual(observation.value, 3)
        self.assertEqual(observation.period_start, "2018-01-01")
        self.assertEqual(observation.product_mapping, "test-map")
        self.assertTrue(observation.evidence_ids)
        self.assertTrue(any("could not be joined" in item for item in result.limitations))

    def test_missing_mapping_is_no_results_without_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._fixture(root)
            result = load_hm_sales_analysis(request_id="r1", schema_version="1.0",
                requested_category="unknown", source_category_map={}, category_mapping_version="test", processed_dir=root)
        self.assertEqual(result.status, "no_results")
        self.assertFalse(result.observations)


if __name__ == "__main__":
    unittest.main()
