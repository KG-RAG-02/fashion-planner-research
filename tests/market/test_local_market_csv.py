from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from market_research_agent import MarketResearchAgent, MarketResearchContext
from market_research_agent.cli import main as cli_main
from market_research_agent.sources.sales.hm_kaggle.hm_sales_adapter import HmHistoricalSalesSource
from market_research_agent.sources.market.local_market_csv import LocalMarketCsvSource


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
            "search_text",
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

    def _context(self, query: str | None = None) -> MarketResearchContext:
        return MarketResearchContext(
            request_id="synthetic-request",
            planning_request={},
            requested_category="Outerwear",
            requested_metrics=("market_size",),
            requested_geography="KR",
            requested_channel="online",
            requested_query=query,
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

    def test_natural_language_query_does_not_filter_structured_rows_and_is_preserved(self) -> None:
        query = "한국의 온라인 아우터 시장 규모를 알려줘"
        row = dict(self.row, search_text="대한민국 온라인 겉옷 시장규모 27SS")
        self._write_rows([row])
        result = MarketResearchAgent(self._source()).run(self._context(query))

        self.assertEqual(len(result.market_data.observations), 1)
        self.assertEqual(result.to_proposal_v1_2_dict()["requested_scope"]["query"], query)

    def test_structured_category_labels_ignore_korean_spacing_but_keep_scope_filter(self) -> None:
        self._write_rows([dict(self.row, metric_name="market size")])
        context = MarketResearchContext(
            request_id="synthetic-request", planning_request={}, requested_category="Outerwear",
            requested_metrics=("market size",), requested_geography="KR", requested_channel="online",
        )
        result = MarketResearchAgent(self._source()).run(context)
        self.assertEqual(result.market_data.status, "ok")

        mismatch = MarketResearchContext(
            request_id="synthetic-request", planning_request={}, requested_category="Footwear",
            requested_metrics=("market_size",), requested_geography="KR", requested_channel="online",
        )
        self.assertEqual(MarketResearchAgent(self._source()).run(mismatch).market_data.status, "no_results")

    def test_raw_query_without_search_text_column_is_preserved_as_limitation(self) -> None:
        self.columns.remove("search_text")
        self._write_rows([self.row])
        report = self._source().research(self._context("한국 온라인 아우터 시장규모"))

        self.assertEqual(report.market_data.status, "partial")
        self.assertTrue(any("structured scope fields" in item for item in report.market_data.limitations))

    def test_cli_preserves_query_without_connecting_out_of_scope_market_csv(self) -> None:
        query = "한국 온라인 아우터 시장규모"
        self._write_rows([dict(self.row, search_text=f"{query} 공식 통계")])
        input_path = self.root / "request.json"
        input_path.write_text(json.dumps({
            "planning_request": {
                "schema_version": "1.0", "request_id": "synthetic-cli-request",
                "query": query, "context": {"category": {"value": "Outerwear"}},
            },
        }), encoding="utf-8")
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = cli_main([
                "--inputs-json", str(input_path),
                "--metrics", "market_size",
                "--proposal-v1-2",
            ])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["requested_scope"]["query"], query)
        self.assertEqual(payload["market_data"]["status"], "no_results")

    def test_missing_scope_metadata_retains_row_as_partial_without_claiming_scope_match(self) -> None:
        row = dict(self.row, channel="", period_start="", period_end="", as_of="", product_mapping_version="")
        self._write_rows([row])
        report = self._source().research(self._context())

        self.assertEqual(report.market_data.status, "no_results")
        self.assertEqual(len(report.market_data.observations), 0)

    def test_requested_scope_filters_nonmatching_categories(self) -> None:
        other = dict(self.row, observation_id="synthetic-observation-2", category="Footwear")
        self._write_rows([other])
        report = self._source().research(self._context())

        self.assertEqual(report.market_data.status, "no_results")
        self.assertEqual(report.market_data.missing_data[0].reason, "no_matching_records")

    def test_requested_year_filters_rows_outside_period(self) -> None:
        context = self._context()
        context = MarketResearchContext(
            request_id=context.request_id, planning_request={}, requested_category=context.requested_category,
            requested_metrics=context.requested_metrics, requested_geography=context.requested_geography,
            requested_channel=context.requested_channel, requested_period="2027",
        )
        self.assertEqual(self._source().research(context).market_data.status, "no_results")

    def test_hm_historical_result_keeps_original_measure_and_evidence(self) -> None:
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
            requested_metrics=("purchase_rows",),
        )
        sales_source = HmHistoricalSalesSource(
            hm_dir, {"Jacket": "Outerwear"}, "synthetic-map-v1", usage_authorized=True
        )
        result = MarketResearchAgent(sales_source).run(context)

        self.assertEqual(result.request_id, "synthetic-combined-request")
        self.assertEqual(result.sales_data.status, "ok")
        self.assertEqual(result.sales_data.observations[0].period_start, "2018-01-01")
        self.assertEqual(result.sales_data.observations[0].metric_name, "purchase_rows")
        self.assertEqual(result.sales_data.observations[0].unit, "purchase_rows")
        self.assertTrue(any("Historical Kaggle" in item for item in result.limitations))
        self.assertTrue(all(item.permission_status.value == "authorized" for item in result.evidence))

    def test_hm_market_size_request_is_not_relabelled_as_purchase_rows(self) -> None:
        hm_source = HmHistoricalSalesSource(
            self.root / "missing-hm-data", {"Jacket": "Outerwear"}, "synthetic-map-v1",
            usage_authorized=True,
        )
        context = MarketResearchContext(
            request_id="synthetic-unsupported-metric", planning_request={},
            requested_category="Outerwear", requested_metrics=("market_size",),
            query_intent="unsupported",
        )
        result = MarketResearchAgent(hm_source).run(context)
        self.assertEqual(result.sales_data.status, "no_results")
        self.assertEqual(result.sales_data.missing_data[0].reason, "not_available_from_source")

    def test_hm_gender_scope_is_not_claimed_without_reviewed_mapping(self) -> None:
        hm_source = HmHistoricalSalesSource(
            self.root / "missing-hm-data", {"Jacket": "Outerwear"}, "synthetic-map-v1",
            usage_authorized=True,
        )
        context = MarketResearchContext(
            request_id="synthetic-gender-scope", planning_request={},
            requested_category="Outerwear", requested_metrics=("purchase_rows",),
            inferred_scope={"gender": "women"},
        )
        result = MarketResearchAgent(hm_source).run(context)
        self.assertEqual(result.sales_data.status, "no_results")
        self.assertIn("gender", result.sales_data.missing_data[0].message)


if __name__ == "__main__":
    unittest.main()
