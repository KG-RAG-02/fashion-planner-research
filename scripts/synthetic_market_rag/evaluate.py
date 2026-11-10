"""Dependency-free lexical baseline and ranking metrics for synthetic fixtures.

This is not an embedding model or production RAG implementation. It exists to
exercise scope filters, test-only permission gates, citations, and ranking
metrics on generated data without sending anything to an external provider.
"""

from __future__ import annotations

import math
import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .generate import TEST_PERMISSION


SYNONYM_EXPANSIONS = {
    "아우터": "outerwear", "외투": "outerwear", "겉옷": "outerwear",
    "신발": "footwear", "한국": "kr", "일본": "jp", "분류": "category mapping taxonomy",
    "매핑": "mapping", "연결되지": "mapping failure",
    "전자상거래": "online transaction amount", "거래액": "market size online transaction amount",
    "매출액": "market size", "시장규모": "market size", "소매판매지수": "retail sales index",
    "지수": "index points", "분기": "quarter",
}


def _tokens(value: str) -> set[str]:
    normalized = value.casefold().replace("_", " ")
    for source, replacement in SYNONYM_EXPANSIONS.items():
        normalized = normalized.replace(source, f" {replacement} ")
    return {token for token in re.findall(r"[a-z0-9]+|[가-힣]+", normalized) if len(token) > 1}


def _scope_match(chunk: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    for field in (
        "category", "geography", "channel", "population", "document_version", "period_label",
        "unit", "currency", "measure_type", "mapping_id", "mapping_version", "period_grain",
    ):
        expected = scope.get(field)
        if expected is not None and chunk.get(field) != expected:
            return False
    metrics = scope.get("metrics") or ()
    if metrics and chunk.get("metric_name") not in metrics:
        return False
    period = scope.get("as_of_or_period")
    if period and chunk.get("period_label") != period:
        return False
    simulated_permission = scope.get("simulated_source_permission_status")
    if simulated_permission and chunk.get("simulated_source_permission_status") != simulated_permission:
        return False
    return True


def _synthetic_index(chunks: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """Index gate: accept only explicitly test-only and simulated-authorized chunks."""
    return tuple(
        chunk for chunk in chunks
        if chunk.get("synthetic") is True
        and chunk.get("data_nature") == "synthetic_test"
        and chunk.get("permission_status") == TEST_PERMISSION
        and chunk.get("simulated_source_permission_status") == "authorized"
        and chunk.get("source_type") == "synthetic_test_market_document"
    )


def lexical_retrieve(bundle: Mapping[str, Any], query_fixture: Mapping[str, Any], *, limit: int = 8) -> dict[str, Any]:
    """Run two permission gates, exact scope filtering, and test-only lexical ranking."""
    if limit < 1:
        raise ValueError("limit must be positive.")
    raw_chunks = bundle.get("chunks", ())
    candidates = tuple(chunk for chunk in raw_chunks if _scope_match(chunk, query_fixture.get("scope", {})))
    text_query = _tokens(str(query_fixture.get("raw_query", "")))
    matching_terms = tuple(
        chunk for chunk in candidates
        if text_query & _tokens(str(chunk.get("text", "")))
    )
    authorized_index = _synthetic_index(matching_terms)
    # Retrieval-time gate is repeated even after the test index has been built.
    permitted = tuple(
        chunk for chunk in authorized_index
        if chunk.get("permission_status") == TEST_PERMISSION
        and chunk.get("simulated_source_permission_status") == "authorized"
        and chunk.get("synthetic") is True
    )
    scored = []
    for chunk in permitted:
        tokens = _tokens(str(chunk.get("text", "")))
        score = len(text_query & tokens) / max(1, len(text_query))
        scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]["evidence_id"]))
    unique = []
    seen_groups = set()
    for score, chunk in scored:
        duplicate_group = chunk.get("duplicate_group") or chunk["evidence_id"]
        if duplicate_group in seen_groups:
            continue
        seen_groups.add(duplicate_group)
        unique.append({
            "evidence_id": chunk["evidence_id"], "locator": chunk["locator"],
            "score": score, "content_hash": chunk["content_hash"],
            "source_id": chunk["source_id"], "source_type": chunk["source_type"],
            "source_name": chunk["source_name"], "page_or_section": chunk["page_or_section"],
            "published_at": chunk["published_at"], "observed_at": chunk["observed_at"],
            "fetched_at": chunk["fetched_at"], "period_start": chunk["period_start"],
            "period_end": chunk["period_end"], "period_grain": chunk["period_grain"],
            "geography": chunk["geography"], "channel": chunk["channel"],
            "category": chunk["category"], "mapping_id": chunk["mapping_id"],
            "mapping_version": chunk["mapping_version"], "document_version": chunk["document_version"],
            "population": chunk["population"], "metric_name": chunk["metric_name"],
            "metric_definition": chunk["metric_definition"], "measure_type": chunk["measure_type"],
            "unit": chunk["unit"], "currency": chunk["currency"],
            "permission_status": chunk["permission_status"],
            "simulated_source_permission_status": chunk["simulated_source_permission_status"],
            "synthetic": chunk["synthetic"], "data_nature": chunk["data_nature"],
            "scenario": chunk.get("scenario"),
        })
    restricted = tuple(
        chunk for chunk in matching_terms
        if chunk.get("simulated_source_permission_status") in {"unknown", "denied"}
    )
    status = "ok" if unique else "error" if restricted else "no_results"
    if unique and restricted:
        status = "partial"
    if any(hit.get("scenario") in {"source_error", "mapping_failure", "missing_data", "conflicting_definition_a", "conflicting_definition_b"} for hit in unique):
        status = "partial"
    return {
        "status": status,
        "hits": unique[:limit],
        "restricted_match_count": len(restricted),
        "missing_data": ["access_denied_or_permission_unknown"] if restricted else [],
        "synthetic": True,
        "data_nature": "synthetic_test",
        "permission_status": TEST_PERMISSION,
        "warning": "합성 평가 자료. 실제 통계·판매·시장 근거가 아니며 사업 의사결정에 사용할 수 없음.",
    }


def _dcg(relevance: list[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(relevance))


def evaluate_fixture(bundle: Mapping[str, Any], fixture: Mapping[str, Any], *, k: int = 8) -> dict[str, Any]:
    """Compute Recall@k, Precision@k, MRR, nDCG, citation accuracy, and negative rejection."""
    if k < 1:
        raise ValueError("k must be positive.")
    from .retriever import SyntheticMarketEvidenceRetriever, retrieval_query_from_fixture
    retriever = SyntheticMarketEvidenceRetriever(bundle)
    query = retrieval_query_from_fixture(fixture, limit=k)
    # Simulated source-permission selection is fixture evaluator metadata, not
    # part of the production RetrievalQuery contract.
    result = retriever.search_synthetic(
        query,
        test_permission_scope=fixture.get("scope", {}).get("simulated_source_permission_status"),
    )
    from market_research_agent import MarketResearchAgent, MarketResearchContext
    workflow_result = MarketResearchAgent(market_retriever=retriever).run(MarketResearchContext(
        request_id=f"synthetic-eval-{fixture['query_id']}", planning_request={},
        requested_query=query.raw_query, requested_category=query.category,
        requested_geography=query.geography, requested_channel=query.channel,
        requested_metrics=query.metrics, requested_period=query.as_of_or_period,
        requested_population=query.population, requested_unit=query.unit,
        requested_currency=query.currency, requested_measure_type=query.measure_type,
        requested_mapping_id=query.mapping_id, requested_mapping_version=query.mapping_version,
        requested_period_grain=query.period_grain, requested_document_version=query.document_version,
    ))
    workflow_query_forwarded = retriever.last_query == query
    synthetic_promotion_count = sum(
        item.evidence_id.startswith("synthetic-rag:") for item in workflow_result.evidence
    ) + len(workflow_result.market_retrieval.hits if workflow_result.market_retrieval else ())
    hits = result["hits"]
    relevant = set(fixture.get("expected_relevant_evidence_ids", ()))
    retrieved_ids = [hit["evidence_id"] for hit in hits]
    true_positive = sum(item in relevant for item in retrieved_ids)
    recall = true_positive / len(relevant) if relevant else (1.0 if not retrieved_ids else 0.0)
    precision = true_positive / k
    reciprocal_rank = next((1 / (index + 1) for index, item in enumerate(retrieved_ids) if item in relevant), 0.0)
    relevance = [int(item in relevant) for item in retrieved_ids]
    ideal = [1] * min(k, len(relevant))
    ndcg = _dcg(relevance) / _dcg(ideal) if ideal and _dcg(ideal) else (1.0 if not retrieved_ids else 0.0)
    expected_locations = {(item["evidence_id"], item["locator"]) for item in fixture.get("expected_evidence", ())}
    cited = {(hit["evidence_id"], hit["locator"]) for hit in hits if hit.get("locator") and hit.get("content_hash")}
    citation_accuracy = len(cited & expected_locations) / len(cited) if cited else (1.0 if not relevant else 0.0)
    hard_negatives = {item["evidence_id"] for item in fixture.get("hard_negatives", ())}
    hard_negative_rejection = 1 - sum(item in hard_negatives for item in retrieved_ids) / len(hard_negatives) if hard_negatives else 1.0
    return {
        "query_id": fixture["query_id"], "expected_status": fixture["expected_status"],
        "actual_status": result["status"], "status_match": result["status"] == fixture["expected_status"],
        "recall_at_k": recall, "precision_at_k": precision, "mrr": reciprocal_rank,
        "ndcg_at_k": ndcg, "citation_accuracy": citation_accuracy,
        "hard_negative_rejection": hard_negative_rejection,
        "permission_violation_count": sum(
            hit.get("permission_status") != TEST_PERMISSION or hit.get("synthetic") is not True
            for hit in hits
        ),
        "raw_query_forwarded": workflow_query_forwarded and retriever.last_query.raw_query == fixture.get("raw_query"),
        "structured_scope_forwarded": workflow_query_forwarded,
        "synthetic_promotion_count": synthetic_promotion_count,
        "retrieved_evidence_ids": retrieved_ids, "restricted_match_count": result["restricted_match_count"],
        "synthetic": True, "data_nature": "synthetic_test", "permission_status": TEST_PERMISSION,
    }


def evaluate_corpus(bundle: Mapping[str, Any], *, k: int = 8) -> dict[str, Any]:
    results = [evaluate_fixture(bundle, fixture, k=k) for fixture in bundle.get("queries", ())]
    count = max(1, len(results))
    metric_names = ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "citation_accuracy", "hard_negative_rejection")
    return {
        "synthetic": True, "data_nature": "synthetic_test", "permission_status": TEST_PERMISSION,
        "warning": "합성 평가 자료. 실제 통계·판매·시장 근거가 아니며 사업 의사결정에 사용할 수 없음. 검색평가 점수는 실제 시장조사 품질이나 사업성과를 나타내지 않음.",
        "k": k, "query_count": len(results),
        "status_accuracy": sum(item["status_match"] for item in results) / count,
        "metrics": {name: sum(item[name] for item in results) / count for name in metric_names},
        "permission_violation_count": sum(item["permission_violation_count"] for item in results),
        "query_forwarding_rate": sum(item["raw_query_forwarded"] and item["structured_scope_forwarded"] for item in results) / count,
        "synthetic_promotion_count": sum(item["synthetic_promotion_count"] for item in results),
        "per_query": results,
    }


def load_bundle(directory: Path) -> dict[str, Any]:
    """Load only a generated synthetic fixture bundle."""
    target = directory.expanduser().resolve()
    if not {"tests", "fixtures", "synthetic_market_rag"}.issubset(target.parts):
        raise ValueError("Fixture input path must be under tests/fixtures/synthetic_market_rag.")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("synthetic") is not True
            or manifest.get("data_nature") != "synthetic_test"
            or manifest.get("permission_status") != TEST_PERMISSION
            or manifest.get("operational_eligible") is not False):
        raise ValueError("Input manifest is not explicitly test-only synthetic data.")
    def read_jsonl(name: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (target / name).read_text(encoding="utf-8").splitlines() if line]
    bundle = {"manifest": manifest, "documents": read_jsonl("documents.jsonl"),
              "chunks": read_jsonl("chunks.jsonl"), "queries": read_jsonl("queries.jsonl")}
    for key in ("documents", "chunks", "queries"):
        for item in bundle[key]:
            if item.get("synthetic") is not True or item.get("data_nature") != "synthetic_test" or item.get("permission_status") != TEST_PERMISSION:
                raise ValueError(f"Non-synthetic or improperly permissioned record in {key}.")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="tests/fixtures/synthetic_market_rag")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--output", help="Optional JSON report path under the synthetic fixture directory.")
    args = parser.parse_args(argv)
    try:
        report = evaluate_corpus(load_bundle(Path(args.input_dir)), k=args.k)
        if args.output:
            output = Path(args.output).expanduser().resolve()
            if "tests" not in output.parts or "fixtures" not in output.parts or "synthetic_market_rag" not in output.parts:
                raise ValueError("Report output path must be under tests/fixtures/synthetic_market_rag.")
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
