from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from market_research_agent import MarketResearchAgent, MarketResearchContext
from market_research_agent.sources.composite import CompositeMarketResearchSource
from market_research_agent.sources.hm_historical.hm_sales_adapter import HmHistoricalSalesSource
from market_research_agent.sources.local_market_csv import LocalMarketCsvSource


class LocalMarketCsvSourceTests(unittest.TestCase):
    """Synthetic temporary files validate ingestion; they are not business data."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.csv_path = self.root / "market.csv"
        self.manifest_path = self.root / "manifest.json"
        self.columns = [
            "observation_id", "metric_name", "measure_type", "value", "unit",
            "evidence_id", "category", "source_category", "period_start",
            "period_end", "period_grain", "as_of", "geography", "channel",
            "currency", "metric_definition", "product_mapping_id",
            "product_mapping_version", "population",
        ]
        self.row = {
            "observation_id": "synthetic-observation-1",
            "metric_name": "market_size",
            "measure_type": "market_estimate",
            "value": "12345",
            "unit": "KRW",
            "evidence_id": "synthetic-source:E-1",
            "category": "Outerwear",
            "source_category": "Outerwear",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "period_grain": "month",
            "as_of": "2026-02-15",
            "geography": "KR",
            "channel": "online",
            "currency": "KRW",
            "metric_definition": "Synthetic test row only",
            "product_mapping_id": "synthetic-map",
            "product_mapping_version": "synthetic-v1",
            "population": "synthetic population",
        }
        self._write_rows([self.row])
        self._write_manifest("authorized")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_rows(self, rows: list[dict[str, str]]) -> None:
        with self.csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.columns)
            writer.writeheader()
            writer.writerows(rows)

    def _write_manifest(self, permission_status: str) -> None:
        self.manifest_path.write_text(json.dumps({
            "source_id": "synthetic-source",
            "source_type": "synthetic_test_source",
            "source_name": "Synthetic test source",
            "manifest_id": "synthetic-manifest-v1",
            "permission_status": permission_status,
            "locator": "synthetic-fixture://local-only",
            "fetched_at": "2026-02-15T00:00:00Z",
        }), encoding="utf-8")

    def _source(self) -> LocalMarketCsvSource:
        return LocalMarketCsvSource(self.csv_path, self.manifest_path)

    def _context(self) -> MarketResearchContext:
        return MarketResearchContext(
            request_id="synthetic-request",
            planning_request={},
            requested_category="Outerwear",
            requested_metrics=("market_size",),
            requested_geography="KR",
            requested_channel="online",
        )

    def test_authorized_rows_become_evidence_linked_observations_and_findings(self) -> None:
        result = MarketResearchAgent(self._source()).run(self._context())
        proposal = result.to_proposal_v1_2_dict()

        self.assertEqual(result.market_data.status, "ok")
        self.assertEqual(proposal["market_data"]["observations"][0]["value"], 12345.0)
        self.assertEqual(proposal["market_data"]["observations"][0]["product_mapping_version"], "synthetic-v1")
        self.assertEqual(result.findings[0].evidence_ids, ("synthetic-source:E-1",))
        self.assertEqual(result.evidence[0].permission_status.value, "authorized")
        self.assertEqual(result.evidence[0].period_start, "2026-01-01")

    def test_unconfirmed_permission_withholds_all_observations(self) -> None:
        self._write_manifest("unknown")
        result = MarketResearchAgent(self._source()).run(self._context())

        self.assertEqual(result.status.value, "error")
        self.assertEqual(result.market_data.status, "error")
        self.assertEqual(result.market_data.observations, ())
        self.assertEqual(result.market_data.missing_data[0].reason, "permission_not_confirmed")

    def test_denied_permission_is_distinct_from_unconfirmed_permission(self) -> None:
        self._write_manifest("denied")
        result = MarketResearchAgent(self._source()).run(self._context())

        self.assertEqual(result.market_data.missing_data[0].reason, "access_denied")
        self.assertIn("MARKET_SOURCE_ACCESS_DENIED", {item.code for item in result.errors})

    def test_missing_scope_metadata_retains_row_as_partial_without_claiming_scope_match(self) -> None:
        row = dict(self.row, channel="", period_start="", period_end="", as_of="", product_mapping_version="")
        self._write_rows([row])
        report = self._source().research(self._context())

        self.assertEqual(report.market_data.status, "partial")
        self.assertEqual(len(report.market_data.observations), 1)
        self.assertTrue(any("no channel" in item for item in report.market_data.limitations))
        self.assertTrue(any("neither a complete period" in item for item in report.market_data.limitations))

    def test_requested_scope_filters_nonmatching_categories(self) -> None:
        other = dict(self.row, observation_id="synthetic-observation-2", category="Footwear")
        self._write_rows([other])
        report = self._source().research(self._context())

        self.assertEqual(report.market_data.status, "no_results")
        self.assertEqual(report.market_data.missing_data[0].reason, "no_matching_records")

    def test_hm_historical_and_market_files_return_together_under_one_request_id(self) -> None:
        hm_dir = self.root / "hm-processed"
        hm_dir.mkdir()
        (hm_dir / "manifest.json").write_text(json.dumps({
            "manifest_id": "synthetic-hm-manifest-v1",
            "observation_period": {"start": "2018-01-01", "end": "2018-01-07"},
            "source_transactions": {"container_sha256": "synthetic-sales-hash"},
            "source_articles": {"container_sha256": "synthetic-catalog-hash"},
        }), encoding="utf-8")
        with (hm_dir / "article_catalog.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[
                "article_id", "product_code", "product_group_name", "product_type_name",
                "colour_group_name", "department_name",
            ])
            writer.writeheader()
            writer.writerow({
                "article_id": "SYN-A1", "product_code": "SYN-P1",
                "product_group_name": "Garment Upper body", "product_type_name": "Jacket",
                "colour_group_name": "Black", "department_name": "Ladieswear",
            })
        with (hm_dir / "weekly_sales_by_article_channel.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["article_id", "purchase_rows", "week_start", "sales_channel_id"])
            writer.writeheader()
            writer.writerow({"article_id": "SYN-A1", "purchase_rows": "7", "week_start": "2018-01-01", "sales_channel_id": "1"})

        context = MarketResearchContext(
            request_id="synthetic-combined-request",
            planning_request={},
            requested_category="Outerwear",
            requested_metrics=("purchase_rows", "market_size"),
            requested_geography="KR",
            requested_channel="online",
        )
        sales_source = HmHistoricalSalesSource(
            hm_dir, {"Jacket": "Outerwear"}, "synthetic-map-v1", usage_authorized=True
        )
        combined = CompositeMarketResearchSource((sales_source, self._source()))
        result = MarketResearchAgent(combined).run(context)

        self.assertEqual(result.request_id, "synthetic-combined-request")
        self.assertEqual(result.sales_data.status, "ok")
        self.assertEqual(result.sales_data.observations[0].period_start, "2018-01-01")
        self.assertEqual(result.market_data.status, "ok")
        self.assertEqual(result.market_data.observations[0].geography, "KR")
        self.assertNotEqual(
            result.sales_data.observations[0].unit,
            result.market_data.observations[0].unit,
        )
        self.assertTrue(any("Historical Kaggle" in item for item in result.limitations))
        self.assertTrue(all(item.permission_status.value == "authorized" for item in result.evidence))


if __name__ == "__main__":
    unittest.main()
