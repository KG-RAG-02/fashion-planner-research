import json
import tempfile
import unittest
from pathlib import Path

from tools.synthetic_market_rag.evaluate import evaluate_corpus, lexical_retrieve, load_bundle
from tools.synthetic_market_rag.generate import (
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
