import json
import tempfile
import unittest
from pathlib import Path

from scripts.synthetic_market_rag.evaluate import evaluate_corpus, lexical_retrieve, load_bundle
from scripts.synthetic_market_rag.retriever import SyntheticMarketEvidenceRetriever, retrieval_query_from_fixture
from scripts.synthetic_market_rag.generate import (
    DEFAULT_DOCUMENT_COUNT, DEFAULT_SEED, MAX_DOCUMENT_COUNT, TEST_PERMISSION,
    SYNTHETIC_WARNING, generate_corpus, write_corpus,
)


class SyntheticMarketRagTests(unittest.TestCase):
    def test_seed_reproduces_complete_bundle(self):
        first = generate_corpus(seed=DEFAULT_SEED, document_count=DEFAULT_DOCUMENT_COUNT)
        second = generate_corpus(seed=DEFAULT_SEED, document_count=DEFAULT_DOCUMENT_COUNT)
        self.assertEqual(first, second)
        self.assertEqual(first["manifest"]["document_count"], 120)
        self.assertEqual(first["manifest"]["query_count"], 22)
        with self.assertRaises(ValueError):
            generate_corpus(document_count=MAX_DOCUMENT_COUNT + 1)

    def test_every_record_and_observation_is_marked_synthetic(self):
        bundle = generate_corpus()
        for kind in ("documents", "chunks", "queries"):
            for record in bundle[kind]:
                self.assertTrue(record["synthetic"])
                self.assertEqual(record["data_nature"], "synthetic_test")
                self.assertEqual(record["permission_status"], TEST_PERMISSION)
                self.assertIn(SYNTHETIC_WARNING, json.dumps(record, ensure_ascii=False))
        for chunk in bundle["chunks"]:
            self.assertTrue(chunk["synthetic_observation"]["synthetic"])
            self.assertEqual(chunk["synthetic_observation"]["permission_status"], TEST_PERMISSION)
            self.assertEqual(chunk["synthetic_observation"]["evidence_id"], chunk["evidence_id"])
            self.assertEqual(chunk["synthetic_observation"]["locator"], chunk["locator"])
            self.assertTrue(chunk["content_hash"])
        ids = {chunk["evidence_id"] for chunk in bundle["chunks"]}
        for query in bundle["queries"]:
            self.assertTrue(query["raw_query"].strip())
            self.assertTrue(query["scope"])
            self.assertTrue(set(query["expected_relevant_evidence_ids"]).issubset(ids))
            for citation in query["expected_evidence"]:
                self.assertIn(citation["evidence_id"], ids)
                self.assertTrue(citation["locator"].startswith("synthetic://"))

    def test_permissions_scope_citations_and_statuses(self):
        bundle = generate_corpus()
        fixtures = {item["query_id"]: item for item in bundle["queries"]}
        denied = lexical_retrieve(bundle, fixtures["Q-ACCESS-DENIED"])
        unknown = lexical_retrieve(bundle, fixtures["Q-PERMISSION-UNKNOWN"])
        self.assertEqual(denied["status"], "error")
        self.assertEqual(unknown["status"], "error")
        self.assertEqual(denied["hits"], [])
        self.assertEqual(unknown["hits"], [])
        self.assertGreater(denied["restricted_match_count"], 0)

        exact = lexical_retrieve(bundle, fixtures["Q-EXACT-MARKET-SIZE"])
        exact_ids = {hit["evidence_id"] for hit in exact["hits"]}
        self.assertIn("synthetic-rag:SYN-DOC-0000:P1", exact_ids)
        self.assertNotIn("synthetic-rag:SYN-DOC-0001:P2", exact_ids)
        self.assertTrue(all(hit["locator"].startswith("synthetic://") for hit in exact["hits"]))
        self.assertEqual(lexical_retrieve(bundle, fixtures["Q-NO-RESULTS"])["status"], "no_results")
        self.assertEqual(lexical_retrieve(bundle, fixtures["Q-SOURCE-ERROR"])["status"], "partial")
        currency = lexical_retrieve(bundle, fixtures["Q-CURRENCY"])
        self.assertTrue(currency["hits"])
        self.assertTrue(all(hit["unit"] == "synthetic_KRW_million" for hit in currency["hits"]))
        self.assertTrue(all(hit["currency"] == "SYNTHETIC_KRW" for hit in currency["hits"]))
        unknown_scope = {**fixtures["Q-CURRENCY"], "scope": {**fixtures["Q-CURRENCY"]["scope"], "population": "unrecorded-population"}}
        self.assertEqual(lexical_retrieve(bundle, unknown_scope)["status"], "no_results")

    def test_synthetic_index_rejects_operational_or_unmarked_records(self):
        bundle = generate_corpus()
        fixture = next(item for item in bundle["queries"] if item["query_id"] == "Q-EXACT-MARKET-SIZE")
        operational = dict(bundle["chunks"][0])
        operational.update(synthetic=False, data_nature="observed", permission_status="authorized", source_type="market_document")
        altered_chunks = [operational if c["evidence_id"] == operational["evidence_id"] else c for c in bundle["chunks"]]
        altered = {**bundle, "chunks": altered_chunks}
        result = lexical_retrieve(altered, fixture)
        self.assertNotIn(operational["evidence_id"], {hit["evidence_id"] for hit in result["hits"]})
        self.assertNotIn("synthetic-rag:SYN-DOC-0008:P2", {hit["evidence_id"] for hit in result["hits"]})

    def test_stale_vintage_conflict_injection_and_metrics(self):
        bundle = generate_corpus()
        results = {item["query_id"]: item for item in evaluate_corpus(bundle)["per_query"]}
        self.assertEqual(results["Q-VINTAGE"]["actual_status"], "ok")
        self.assertNotIn("synthetic-rag:SYN-DOC-0012:P2", results["Q-VINTAGE"]["retrieved_evidence_ids"])
        self.assertEqual(results["Q-CONFLICT"]["actual_status"], "partial")
        injection = next(item for item in bundle["queries"] if item["query_id"] == "Q-PROMPT-INJECTION")
        self.assertIn("IGNORE ALL RULES", injection["raw_query"])
        self.assertIn("untrusted_document_instruction", next(c for c in bundle["chunks"] if c["scenario"] == "prompt_injection_untrusted"))
        report = evaluate_corpus(bundle)
        self.assertTrue(report["synthetic"])
        for key in ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "citation_accuracy", "hard_negative_rejection"):
            self.assertIn(key, report["metrics"])
            self.assertGreaterEqual(report["metrics"][key], 0)
            self.assertLessEqual(report["metrics"][key], 1)
        self.assertEqual(report["permission_violation_count"], 0)
        self.assertEqual(report["query_forwarding_rate"], 1)
        self.assertEqual(report["synthetic_promotion_count"], 0)

    def test_synthetic_adapter_uses_query_contract_but_never_promotes_hits(self):
        from market_research_agent import MarketResearchAgent, MarketResearchContext
        from market_research_agent.domain.models import PermissionStatus
        from market_research_agent.sources.market.evidence_retrieval import RetrievalQuery

        bundle = generate_corpus()
        adapter = SyntheticMarketEvidenceRetriever(bundle)
        fixture = next(item for item in bundle["queries"] if item["query_id"] == "Q-EXACT-MARKET-SIZE")
        query = retrieval_query_from_fixture(fixture)
        direct_result = adapter.search_synthetic(query)
        self.assertTrue(direct_result["hits"])
        self.assertTrue(all(hit["permission_status"] == TEST_PERMISSION for hit in direct_result["hits"]))
        hit = direct_result["hits"][0]
        self.assertTrue(hit["evidence_id"] and hit["source_id"] and hit["source_name"])
        self.assertTrue(hit["locator"].startswith("synthetic://") and hit["content_hash"])
        self.assertTrue(hit["page_or_section"] and hit["period_start"] and hit["period_end"])
        self.assertEqual((hit["geography"], hit["channel"], hit["category"]), ("KR", "online", "Outerwear"))
        self.assertTrue(hit["mapping_id"] and hit["mapping_version"] and hit["metric_definition"])

        result = MarketResearchAgent(market_retriever=adapter).run(MarketResearchContext(
            request_id="synthetic-rag-workflow", planning_request={},
            requested_query=query.raw_query, requested_category=query.category,
            requested_geography=query.geography, requested_channel=query.channel,
            requested_metrics=query.metrics, requested_period=query.as_of_or_period,
            requested_population=query.population, requested_unit=query.unit,
            requested_currency=query.currency, requested_measure_type=query.measure_type,
            requested_mapping_id=query.mapping_id, requested_mapping_version=query.mapping_version,
            requested_period_grain=query.period_grain, requested_document_version=query.document_version,
        ))
        self.assertIsInstance(adapter.last_query, RetrievalQuery)
        self.assertEqual(adapter.last_query, query)
        self.assertEqual(result.market_retrieval.status, "no_results")
        self.assertEqual(result.market_retrieval.hits, ())
        self.assertEqual(result.evidence, ())
        proposal = result.to_proposal_v1_3_dict()
        self.assertEqual(proposal["market_retrieval"]["hits"], [])
        self.assertNotIn("market_retrieval", result.to_dict())
        self.assertNotIn(PermissionStatus.AUTHORIZED, [hit.get("permission_status") for hit in direct_result["hits"]])

        default_result = MarketResearchAgent().run(MarketResearchContext(
            request_id="synthetic-rag-default", planning_request={}, requested_query="market size",
        ))
        self.assertEqual(default_result.sales_retrieval.missing_data, ("retriever_not_configured",))
        self.assertEqual(default_result.market_retrieval.missing_data, ("retriever_not_configured",))
        self.assertEqual(default_result.sales_retrieval.hits, ())
        self.assertEqual(default_result.market_retrieval.hits, ())

    def test_bad_retriever_hits_are_error_and_do_not_erase_other_domain(self):
        from market_research_agent import EvidenceRecord, MarketResearchAgent, MarketResearchContext
        from market_research_agent.domain.models import PermissionStatus
        from market_research_agent.sources.market.evidence_retrieval import EvidenceChunk, RetrievedEvidence

        class InvalidRetriever:
            def retrieve(self, _query):
                return ({"synthetic": True, "permission_status": TEST_PERMISSION},)

        chunk = EvidenceChunk(
            evidence_id="E-market-valid", source_id="test-source", source_name="Test-only unit fixture",
            text="Unit-test evidence", locator="test://section/1", permission_status=PermissionStatus.AUTHORIZED,
            content_hash="test-hash", page_or_section="section 1",
        )

        class ValidRetriever:
            def retrieve(self, _query):
                return (RetrievedEvidence(chunk, 0.9),)

        result = MarketResearchAgent(sales_retriever=InvalidRetriever(), market_retriever=ValidRetriever()).run(
            MarketResearchContext(request_id="mixed-retrieval", planning_request={}, requested_query="test query")
        )
        self.assertEqual(result.sales_retrieval.status, "error")
        self.assertEqual(result.sales_retrieval.missing_data, ("unauthorized_or_invalid_hits_omitted",))
        self.assertEqual(result.market_retrieval.status, "ok")
        self.assertEqual({item.evidence_id for item in result.evidence}, {"E-market-valid"})
        self.assertIn("RETRIEVAL_INVALID_HITS", {item.code for item in result.errors})

    def test_retrieval_empty_access_denied_and_source_error_are_distinct(self):
        from market_research_agent import MarketResearchAgent, MarketResearchContext

        class EmptyRetriever:
            def retrieve(self, _query):
                return ()

        class DeniedRetriever:
            def retrieve(self, _query):
                raise PermissionError("sensitive detail must not escape")

        class BrokenRetriever:
            def retrieve(self, _query):
                raise RuntimeError("credential-like detail must not escape")

        def run(retriever):
            return MarketResearchAgent(market_retriever=retriever).run(MarketResearchContext(
                request_id="retrieval-status", planning_request={}, requested_query="test query",
            ))

        self.assertEqual(run(EmptyRetriever()).market_retrieval.status, "no_results")
        denied = run(DeniedRetriever())
        self.assertEqual((denied.market_retrieval.status, denied.market_retrieval.missing_data), ("error", ("access_denied",)))
        broken = run(BrokenRetriever())
        self.assertEqual((broken.market_retrieval.status, broken.market_retrieval.missing_data), ("error", ("retrieval_error",)))
        messages = " ".join(error.message for error in (*denied.errors, *broken.errors))
        self.assertNotIn("sensitive detail", messages)
        self.assertNotIn("credential-like detail", messages)

    def test_fixture_io_is_locked_to_test_directory(self):
        with tempfile.TemporaryDirectory() as root:
            safe = Path(root) / "tests" / "fixtures" / "synthetic_market_rag"
            write_corpus(safe, seed=37, document_count=20)
            loaded = load_bundle(safe)
            self.assertEqual(loaded["manifest"]["seed"], 37)
            with self.assertRaises(ValueError):
                write_corpus(Path(root) / "data" / "market", document_count=20)
            with self.assertRaises(ValueError):
                load_bundle(Path(root) / "data" / "market")


if __name__ == "__main__":
    unittest.main()
