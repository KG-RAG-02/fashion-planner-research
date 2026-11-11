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
    "지수": "index points", "지수점": "index points", "분기": "quarter",
    "오류": "error source_error", "조회 오류": "retrieval error source error",
    "자료 없음": "missing data no results", "미확보": "unavailable missing data",
}


def _tokens(value: str) -> set[str]:
    normalized = value.casefold().replace("_", " ")
    for source, replacement in sorted(SYNONYM_EXPANSIONS.items(), key=lambda item: len(item[0]), reverse=True):
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


def _retrieval_role(chunk: Mapping[str, Any]) -> str:
    """Resolve the generator's explicit evidence role, with legacy compatibility."""
    role = chunk.get("retrieval_role")
    if role in {"metric_definition", "observation", "supplementary_context"}:
        return str(role)
    section = str(chunk.get("page_or_section", ""))
    if section == "section-1":
        return "metric_definition"
    if section == "section-2":
        return "observation"
    return "supplementary_context"


def lexical_retrieve(bundle: Mapping[str, Any], query_fixture: Mapping[str, Any], *, limit: int = 8) -> dict[str, Any]:
    """Apply scope and permission gates, then rank complementary primary evidence by document."""
    if limit < 1:
        raise ValueError("limit must be positive.")
    raw_chunks = bundle.get("chunks", ())
    requested_scope = dict(query_fixture.get("scope", {}))
    scoped_candidates = tuple(chunk for chunk in raw_chunks if _scope_match(chunk, requested_scope))
    candidates = scoped_candidates
    raw_query = str(query_fixture.get("raw_query", ""))
    text_query = _tokens(raw_query)
    # Status terms are not interchangeable: when the query asks about source
    # errors, a no-data example without an error signal is not a match.
    if re.search(r"오류|source error|retrieval error", raw_query, re.IGNORECASE):
        candidates = tuple(chunk for chunk in candidates if re.search(
            r"오류|source[_ ]error|retrieval[_ ]error", str(chunk.get("text", "")), re.IGNORECASE,
        ))
    status_filtered_count = len(scoped_candidates) - len(candidates)
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
        role = _retrieval_role(chunk)
        # Supplementary chunks are generated for chunking/format variation and
        # repeat scope boilerplate; they are not direct definition or value
        # evidence for this analysis retriever.
        if role in {"metric_definition", "observation"}:
            scored.append((score, chunk))

    by_document: dict[str, list[tuple[float, Mapping[str, Any]]]] = {}
    for item in scored:
        document_id = str(item[1].get("document_id") or item[1]["evidence_id"].split(":P", 1)[0])
        by_document.setdefault(document_id, []).append(item)
    ranked_documents = sorted(
        by_document,
        key=lambda document_id: (
            -max(score for score, _ in by_document[document_id]),
            document_id,
        ),
    )
    unique = []
    seen_groups: dict[str, str] = {}
    duplicate_excluded = 0
    for document_id in ranked_documents:
        # These generated records have two complementary primary facets:
        # definition and observation. Keep both when they match, but do not
        # let a document consume more than two result slots.
        document_chunks = sorted(
            by_document[document_id],
            key=lambda item: (-item[0], item[1]["evidence_id"]),
        )[:2]
        for score, chunk in document_chunks:
            duplicate_group = chunk.get("duplicate_group") or chunk["evidence_id"]
            prior_document = seen_groups.get(duplicate_group)
            if prior_document is not None and prior_document != document_id:
                duplicate_excluded += 1
                continue
            seen_groups[duplicate_group] = document_id
            unique.append({
                "evidence_id": chunk["evidence_id"], "locator": chunk["locator"],
                "score": score, "content_hash": chunk["content_hash"],
                "source_id": chunk["source_id"], "source_type": chunk["source_type"],
                "source_name": chunk["source_name"], "page_or_section": chunk["page_or_section"],
                "retrieval_role": _retrieval_role(chunk),
                "published_at": chunk["published_at"], "observed_at": chunk["observed_at"],
                "fetched_at": chunk["fetched_at"], "period_start": chunk["period_start"],
                "period_end": chunk["period_end"], "period_label": chunk["period_label"], "period_grain": chunk["period_grain"],
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
    invalid_or_restricted_count = len(matching_terms) - len(authorized_index)
    supplementary_count = sum(_retrieval_role(chunk) == "supplementary_context" for chunk in permitted)
    primary_authorized_count = len(scored)
    return {
        "status": status,
        "hits": unique[:limit],
        "restricted_match_count": len(restricted),
        "missing_data": ["access_denied_or_permission_unknown"] if restricted else [],
        "synthetic": True,
        "data_nature": "synthetic_test",
        "permission_status": TEST_PERMISSION,
        "warning": "합성 평가 자료. 실제 통계·판매·시장 근거가 아니며 사업 의사결정에 사용할 수 없음.",
        "query_trace": {
            "raw_query": raw_query,
            "requested_scope": requested_scope,
            "applied_hard_filters": sorted(key for key, value in requested_scope.items() if value not in (None, "", (), [])),
            "retrieval_strategy": "scope_and_permission_prefilter_then_document_rank_two_primary_evidence_facets",
            "candidate_counts": {
                "total_chunks": len(raw_chunks), "scope_matched": len(scoped_candidates),
                "lexical_matched": len(matching_terms), "authorized_synthetic": len(authorized_index),
                "primary_evidence": primary_authorized_count, "returned": min(limit, len(unique)),
            },
            "excluded_counts": {
                "scope_mismatch": len(raw_chunks) - len(scoped_candidates),
                "status_signal_filter": status_filtered_count,
                "no_lexical_overlap": len(candidates) - len(matching_terms),
                "permission_or_synthetic_gate": invalid_or_restricted_count,
                "supplementary_context": supplementary_count,
                "duplicate_document": duplicate_excluded,
                "per_document_facet_cap": max(0, primary_authorized_count - len(unique) - duplicate_excluded),
            },
        },
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
    workflow_query_forwarded = bool(retriever.last_query) and all(
        getattr(retriever.last_query, field) == getattr(query, field)
        for field in (
            "raw_query", "category", "geography", "channel", "metrics", "as_of_or_period",
            "population", "unit", "currency", "measure_type", "mapping_id", "mapping_version",
            "period_grain", "document_version",
        )
    )
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
    scope = fixture.get("scope", {})
    scope_field_map = {
        "category": "category", "geography": "geography", "channel": "channel",
        "metrics": "metric_name", "population": "population", "unit": "unit",
        "currency": "currency", "measure_type": "measure_type", "mapping_id": "mapping_id",
        "mapping_version": "mapping_version", "period_grain": "period_grain",
        "document_version": "document_version", "as_of_or_period": "period_label",
    }
    scope_mismatch_hit_count = sum(
        any(hit.get(field) not in (value if isinstance(value, list) else [value])
            for field, value in ((scope_field_map[key], expected) for key, expected in scope.items() if key in scope_field_map and expected not in (None, (), [])))
        for hit in hits
    )
    evidence_lineage_invalid_count = sum(
        not (hit.get("evidence_id") and hit.get("locator") and hit.get("content_hash")) for hit in hits
    )
    query_id = str(fixture["query_id"]).casefold()
    query_group = next((group for marker, group in (
        ("permission", "permission"), ("access", "permission"), ("mapping", "mapping"),
        ("index", "metric_definition"), ("currency", "metric_definition"),
        ("period", "scope_filter"), ("channel", "scope_filter"), ("geography", "scope_filter"),
        ("population", "scope_filter"), ("no-results", "no_results"), ("zero-vs-missing", "data_quality"),
        ("vintage", "revision_vintage"), ("duplicate", "revision_vintage"),
        ("conflict", "definition_conflict"), ("prompt-injection", "untrusted_content"),
        ("source-error", "source_error"), ("synonym", "synonym"),
    ) if marker in query_id), "exact_retrieval")
    return {
        "query_id": fixture["query_id"], "query_group": query_group, "expected_status": fixture["expected_status"],
        "actual_status": result["status"], "status_match": result["status"] == fixture["expected_status"],
        "recall_at_k": recall, "precision_at_k": precision, "mrr": reciprocal_rank,
        "ndcg_at_k": ndcg, "citation_accuracy": citation_accuracy,
        "hard_negative_rejection": hard_negative_rejection,
        "hard_negative_retrieval_count": sum(item in hard_negatives for item in retrieved_ids),
        "scope_mismatch_hit_count": scope_mismatch_hit_count,
        "evidence_lineage_invalid_count": evidence_lineage_invalid_count,
        "permission_violation_count": sum(
            hit.get("permission_status") != TEST_PERMISSION or hit.get("synthetic") is not True
            for hit in hits
        ),
        "raw_query_forwarded": workflow_query_forwarded and retriever.last_query.raw_query == fixture.get("raw_query"),
        "structured_scope_forwarded": workflow_query_forwarded,
        "synthetic_promotion_count": synthetic_promotion_count,
        "retrieved_evidence_ids": retrieved_ids, "restricted_match_count": result["restricted_match_count"],
        "query_trace": result.get("query_trace"),
        "synthetic": True, "data_nature": "synthetic_test", "permission_status": TEST_PERMISSION,
    }


def evaluate_corpus(bundle: Mapping[str, Any], *, k: int = 8) -> dict[str, Any]:
    results = [evaluate_fixture(bundle, fixture, k=k) for fixture in bundle.get("queries", ())]
    count = max(1, len(results))
    metric_names = ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "citation_accuracy", "hard_negative_rejection")
    positive = [item for item in results if item["expected_status"] in {"ok", "partial"}]
    label_precision_ceiling = sum(
        min(k, len(item.get("expected_relevant_evidence_ids", ()))) / k
        for item in bundle.get("queries", ())
    ) / max(1, len(bundle.get("queries", ())))
    positive_label_precision_ceiling = sum(
        min(k, len(item.get("expected_relevant_evidence_ids", ()))) / k
        for item in bundle.get("queries", ()) if item.get("expected_status") in {"ok", "partial"}
    ) / max(1, len(positive))
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        groups.setdefault(item["query_group"], []).append(item)
    group_metrics = {
        name: {
            "query_count": len(items),
            "status_accuracy": sum(item["status_match"] for item in items) / len(items),
            "recall_at_k": sum(item["recall_at_k"] for item in items) / len(items),
            "precision_at_k": sum(item["precision_at_k"] for item in items) / len(items),
            "hard_negative_retrieval_count": sum(item["hard_negative_retrieval_count"] for item in items),
        }
        for name, items in sorted(groups.items())
    }
    return {
        "synthetic": True, "data_nature": "synthetic_test", "permission_status": TEST_PERMISSION,
        "warning": "합성 평가 자료. 실제 통계·판매·시장 근거가 아니며 사업 의사결정에 사용할 수 없음. 검색평가 점수는 실제 시장조사 품질이나 사업성과를 나타내지 않음.",
        "k": k, "query_count": len(results),
        "status_accuracy": sum(item["status_match"] for item in results) / count,
        "metrics": {name: sum(item[name] for item in results) / count for name in metric_names},
        "positive_query_count": len(positive),
        "positive_subset_metrics": {
            "recall_at_k": sum(item["recall_at_k"] for item in positive) / max(1, len(positive)),
            "precision_at_k": sum(item["precision_at_k"] for item in positive) / max(1, len(positive)),
        },
        "precision_at_k_label_ceiling": label_precision_ceiling,
        "positive_precision_at_k_label_ceiling": positive_label_precision_ceiling,
        "by_query_group": group_metrics,
        "permission_violation_count": sum(item["permission_violation_count"] for item in results),
        "hard_negative_retrieval_count": sum(item["hard_negative_retrieval_count"] for item in results),
        "scope_mismatch_hit_count": sum(item["scope_mismatch_hit_count"] for item in results),
        "evidence_lineage_invalid_count": sum(item["evidence_lineage_invalid_count"] for item in results),
        "evidence_lineage_coverage": 1 - sum(item["evidence_lineage_invalid_count"] for item in results) / max(1, sum(len(item["retrieved_evidence_ids"]) for item in results)),
        "query_forwarding_rate": sum(item["raw_query_forwarded"] and item["structured_scope_forwarded"] for item in results) / count,
        "synthetic_promotion_count": sum(item["synthetic_promotion_count"] for item in results),
        "unsupported_factual_claim_rate": "not_measured_by_retrieval_only_fixture",
        "failed_status_query_ids": [item["query_id"] for item in results if not item["status_match"]],
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


def compare_reports(current: Mapping[str, Any], previous: Mapping[str, Any], *, previous_name: str) -> dict[str, Any]:
    """Compare reports only when k and the labeled query set are identical."""
    for report in (current, previous):
        if (
            report.get("synthetic") is not True
            or report.get("data_nature") != "synthetic_test"
            or report.get("permission_status") != TEST_PERMISSION
        ):
            raise ValueError("Only explicitly test-only synthetic reports may be compared.")
    if current.get("k") != previous.get("k"):
        raise ValueError("Cannot compare retrieval reports with different k values.")
    current_rows = {row["query_id"]: row for row in current.get("per_query", ())}
    previous_rows = {row["query_id"]: row for row in previous.get("per_query", ())}
    if not current_rows or current_rows.keys() != previous_rows.keys():
        raise ValueError("Cannot compare retrieval reports with different query sets.")
    metric_keys = ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "citation_accuracy", "hard_negative_rejection")
    rank_changes = []
    for query_id, row in current_rows.items():
        old = previous_rows[query_id]
        old_ids = old["retrieved_evidence_ids"]
        new_ids = row["retrieved_evidence_ids"]
        if old_ids == new_ids:
            continue
        rank_changes.append({
            "query_id": query_id,
            "before_evidence_ids": old_ids,
            "after_evidence_ids": new_ids,
            "added_evidence_ids": [item for item in new_ids if item not in old_ids],
            "removed_evidence_ids": [item for item in old_ids if item not in new_ids],
            "before_precision_at_k": old["precision_at_k"],
            "after_precision_at_k": row["precision_at_k"],
            "reason": "primary definition/observation facets are ranked together; supplementary context no longer fills result slots",
        })
    groups = {}
    for name, new in current.get("by_query_group", {}).items():
        old = previous.get("by_query_group", {}).get(name)
        if not old:
            continue
        groups[name] = {
            key: {"before": old[key], "after": new[key], "delta": new[key] - old[key]}
            for key in ("query_count", "status_accuracy", "recall_at_k", "precision_at_k", "hard_negative_retrieval_count")
        }
    return {
        "report": previous_name,
        "same_query_set": True,
        "before": {"status_accuracy": previous["status_accuracy"], "metrics": previous["metrics"], "positive_subset_metrics": previous["positive_subset_metrics"]},
        "after": {"status_accuracy": current["status_accuracy"], "metrics": current["metrics"], "positive_subset_metrics": current["positive_subset_metrics"]},
        "delta": {
            "status_accuracy": current["status_accuracy"] - previous["status_accuracy"],
            "metrics": {key: current["metrics"][key] - previous["metrics"][key] for key in metric_keys},
            "positive_subset_metrics": {
                key: current["positive_subset_metrics"][key] - previous["positive_subset_metrics"][key]
                for key in ("recall_at_k", "precision_at_k")
            },
        },
        "rank_changed_query_count": len(rank_changes),
        "rank_changes": rank_changes,
        "by_query_group": groups,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="tests/fixtures/synthetic_market_rag")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--output", help="Optional JSON report path under the synthetic fixture directory.")
    parser.add_argument("--compare-to", help="Optional prior report path under the synthetic fixture directory; query set and k must match.")
    args = parser.parse_args(argv)
    try:
        report = evaluate_corpus(load_bundle(Path(args.input_dir)), k=args.k)
        if args.compare_to:
            previous_path = Path(args.compare_to).expanduser().resolve()
            if not {"tests", "fixtures", "synthetic_market_rag"}.issubset(previous_path.parts):
                raise ValueError("Comparison report must be under tests/fixtures/synthetic_market_rag.")
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            report["comparison_to"] = compare_reports(report, previous, previous_name=previous_path.name)
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
