from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from market_research_agent import (
    EvidenceRecord, MarketDataResult, MarketObservation, MarketResearchAgent,
    MarketResearchContext, PermissionStatus, ResultStatus, SourceReport,
)
from market_research_agent.sources.market.evidence_retrieval import (
    EvidenceChunk, RetrievedEvidence,
)
from market_research_agent.sources.sales.hm_kaggle.hm_sales_adapter import HmHistoricalSalesSource
from market_research_agent.sources.market.analysis import calculate_market_series_change


class DeterministicAnalysisTests(unittest.TestCase):
    def _observation(self, oid: str, start: str, end: str, value: float) -> MarketObservation:
        return MarketObservation(
            observation_id=oid, metric_name="retail_sales_index", measure_type="aggregate_index",
            value=value, unit="index", evidence_ids=(f"E-{oid}",),
            metric_definition="Synthetic monthly index fixture", period_start=start,
            period_end=end, period_grain="month", geography="KR", channel="online",
            category="Outerwear", product_mapping_id="test-map", product_mapping_version="v1",
            population="Synthetic scope population",
        )

    def test_index_change_has_formula_and_input_lineage(self) -> None:
        records = calculate_market_series_change(
            self._observation("a", "2026-01-01", "2026-01-31", 100),
            self._observation("b", "2026-02-01", "2026-02-28", 110),
        )
        self.assertAlmostEqual(records[0].value, 10)
        self.assertAlmostEqual(records[1].value, 10)
        self.assertTrue(all(item.input_observation_ids == ("a", "b") for item in records))
        self.assertTrue(all(item.evidence_ids == ("E-a", "E-b") for item in records))

    def test_index_change_rejects_mismatched_scope(self) -> None:
        first = self._observation("a", "2026-01-01", "2026-01-31", 100)
        second = self._observation("b", "2026-02-01", "2026-02-28", 110)
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, "not comparable"):
            calculate_market_series_change(first, replace(second, channel="offline"))

    def test_index_change_rejects_missing_period_gap(self) -> None:
        with self.assertRaisesRegex(ValueError, "not contiguous"):
            calculate_market_series_change(
                self._observation("a", "2026-01-01", "2026-01-31", 100),
                self._observation("b", "2026-03-01", "2026-03-31", 110),
            )

    def test_calculations_only_appear_in_explicit_v1_3_proposal(self) -> None:
        source = self._observation("a", "2026-01-01", "2026-01-31", 100)
        current = self._observation("b", "2026-02-01", "2026-02-28", 110)

        class StaticSource:
            def research(self, _context):
                return SourceReport(
                    status=ResultStatus.OK,
                    evidence=tuple(EvidenceRecord(
                        evidence_id=f"E-{oid}", source_id="synthetic", source_type="test",
                        source_name="Synthetic test only", permission_status=PermissionStatus.AUTHORIZED,
                    ) for oid in ("a", "b")),
                    market_data=MarketDataResult("ok", (source, current)),
                )

        result = MarketResearchAgent(StaticSource()).run(MarketResearchContext(
            request_id="synthetic-request", planning_request={},
            requested_metrics=("retail_sales_index_change",),
        ))
        self.assertNotIn("calculations", result.to_proposal_v1_2_dict()["market_data"])
        proposed = result.to_proposal_v1_3_dict()
        self.assertEqual(proposed["schema_version"], "1.3-proposal")
        self.assertEqual(len(proposed["market_data"]["calculations"]), 2)
        self.assertEqual(json.loads(json.dumps(proposed))["market_data"]["calculations"][0]["formula_version"], "1.0")
        schema = json.loads((Path(__file__).parents[1] / "contracts/schemas/proposals/sales-market-data-result-1.3-proposal.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(proposed), set(schema["required"]))
        self.assertEqual(set(proposed), set(schema["properties"]))


class RetrievalAndPermissionTests(unittest.TestCase):
    def test_raw_query_and_scope_reach_authorized_retrievers(self) -> None:
        chunk = EvidenceChunk(
            evidence_id="E-doc-1", source_id="approved-docs", source_name="Synthetic approved guide",
            text="Synthetic explanatory excerpt", locator="fixture://page/2",
            permission_status=PermissionStatus.AUTHORIZED, page_or_section="page 2", content_hash="abc",
            published_at="2026-01-02T00:00:00Z", observed_at="2025-12-31T00:00:00Z",
            fetched_at="2026-01-03T00:00:00Z", period_start="2025-10-01", period_end="2025-12-31",
            period_grain="quarter", population="test population", unit="index points", currency=None,
            measure_type="aggregate_index", metric_name="retail_sales_index",
            metric_definition="Synthetic index definition", mapping_id="test-map", mapping_version="v2",
        )

        class Retriever:
            def __init__(self):
                self.query = None

            def retrieve(self, query):
                self.query = query
                return (RetrievedEvidence(chunk, 0.9),)

        retriever = Retriever()
        result = MarketResearchAgent(sales_retriever=retriever).run(MarketResearchContext(
            request_id="synthetic-request", planning_request={}, requested_query="방수 아노락 시장 규모",
            requested_category="Outerwear", requested_metrics=("market_size",),
            requested_period="2026-Q1", requested_geography="KR", requested_channel="online",
            requested_population="online shoppers", requested_unit="KRW million", requested_currency="KRW",
            requested_measure_type="market_estimate", requested_mapping_id="taxonomy-kr-v2",
            requested_mapping_version="2.0", requested_period_grain="quarter",
            requested_document_version="release-3",
        ))
        self.assertEqual(retriever.query.raw_query, "방수 아노락 시장 규모")
        self.assertEqual(retriever.query.category, "Outerwear")
        self.assertEqual(retriever.query.geography, "KR")
        self.assertEqual(retriever.query.channel, "online")
        self.assertEqual(retriever.query.metrics, ("market_size",))
        self.assertEqual(retriever.query.as_of_or_period, "2026-Q1")
        self.assertEqual(retriever.query.population, "online shoppers")
        self.assertEqual(retriever.query.unit, "KRW million")
        self.assertEqual(retriever.query.currency, "KRW")
        self.assertEqual(retriever.query.measure_type, "market_estimate")
        self.assertEqual(retriever.query.mapping_id, "taxonomy-kr-v2")
        self.assertEqual(retriever.query.mapping_version, "2.0")
        self.assertEqual(retriever.query.period_grain, "quarter")
        self.assertEqual(retriever.query.document_version, "release-3")
        payload = result.to_proposal_v1_3_dict()
        self.assertEqual(payload["sales_retrieval"]["hits"][0]["chunk"]["page_or_section"], "page 2")
        retrieved_chunk = payload["sales_retrieval"]["hits"][0]["chunk"]
        self.assertEqual(retrieved_chunk["period_start"], "2025-10-01")
        self.assertEqual(retrieved_chunk["population"], "test population")
        self.assertEqual(retrieved_chunk["metric_name"], "retail_sales_index")
        self.assertEqual(retrieved_chunk["mapping_id"], "test-map")
        self.assertNotIn("sales_retrieval", result.to_dict())
        self.assertIn("E-doc-1", {item.evidence_id for item in result.evidence})

    def test_owner_authorized_hm_source_reports_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "does-not-exist"
            source = HmHistoricalSalesSource(root, {"Jacket": "Outerwear"}, "map-v1")
            report = source.research(MarketResearchContext(
                request_id="synthetic-request", planning_request={}, requested_category="Outerwear",
            ))
        self.assertEqual(report.status, ResultStatus.NO_RESULTS)
        self.assertEqual(report.sales_data.missing_data[0].reason, "source_not_configured")
        self.assertTrue(source.usage_authorized)


if __name__ == "__main__":
    unittest.main()
