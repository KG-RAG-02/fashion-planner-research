#!/usr/bin/env python3
"""Evaluate the versioned synthetic RAG analysis suite without changing gold labels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from market_research_agent.synthetic_rag import analyze_synthetic_request, load_fixture_bundle  # noqa: E402


def evaluate(suite_path: Path) -> dict:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    if suite.get("suite_version") != "synthetic-rag-analysis-eval/1.0":
        raise ValueError("unsupported evaluation suite version")
    bundle = load_fixture_bundle()
    rows = []
    positive_precisions = []
    positive_recalls = []
    full_scope_recalls = []
    document_precisions = []
    document_recalls = []
    status_hits = 0
    by_type: dict[str, dict] = {}
    hard_negative_returned = 0
    evidence_ok = 0
    evidence_total = 0
    for case in suite["cases"]:
        case_type = case.get("type", "unspecified")
        type_metrics = by_type.setdefault(case_type, {
            "query_count": 0, "status_correct": 0, "precision_values": [], "recall_values": [],
            "document_precision_values": [], "document_recall_values": [],
        })
        type_metrics["query_count"] += 1
        result = analyze_synthetic_request(case["request"], mode="synthetic_test", bundle=bundle)
        domain = case.get("domain")
        returned = result.get("retrieval", {}).get("returned_observation_ids", []) if domain in {"market_like", "sales_like"} else []
        expected = case.get("expected_observation_ids", [])
        returned_docs = [hit["document_id"] for hit in result.get("retrieval", {}).get("document_hits", [])]
        expected_docs = case.get("expected_document_ids", [])
        matched = len(set(returned) & set(expected))
        if expected_docs:
            top_docs = returned_docs[:5]
            precision = len(set(top_docs) & set(expected_docs)) / len(top_docs) if top_docs else 0.0
            recall = len(set(returned_docs) & set(expected_docs)) / len(expected_docs)
            document_precisions.append(precision)
            document_recalls.append(recall)
            type_metrics["document_precision_values"].append(precision)
            type_metrics["document_recall_values"].append(recall)
        if expected:
            selected = returned[:5]
            precision = len(set(selected) & set(expected)) / len(selected) if selected else 0.0
            recall = len(set(selected) & set(expected)) / len(expected)
            recall_ceiling = min(5, len(expected)) / len(expected)
            positive_precisions.append(precision)
            positive_recalls.append(recall)
            full_scope_recalls.append(matched / len(expected))
            type_metrics["precision_values"].append(precision)
            type_metrics["recall_values"].append(recall)
        unexpected = set(returned) - set(expected)
        unexpected_docs = set(returned_docs) - set(expected_docs)
        hard_negative_returned += len(unexpected)
        hard_negative_returned += len(unexpected_docs)
        linked = set(result.get("retrieval", {}).get("returned_evidence_ids", []))
        item_evidence = {eid for row in result.get(domain, {}).get("observations", []) for eid in row.get("evidence_ids", [])} if domain in {"market_like", "sales_like"} else set()
        evidence_total += len(item_evidence)
        evidence_ok += len(item_evidence & linked)
        document_linked_ids = {hit["evidence_id"] for hit in result.get("retrieval", {}).get("document_hits", [])}
        document_evidence_total = len(document_linked_ids)
        evidence_total += document_evidence_total
        evidence_ok += sum(
            1 for hit in result.get("retrieval", {}).get("document_hits", [])
            if bundle["document_evidence"].get(hit["evidence_id"], {}).get("content_hash") == hit.get("content_hash")
        )
        status_hit = result.get("status") == case["expected_status"] and result.get("reason") == case.get("expected_reason")
        status_hits += int(status_hit)
        type_metrics["status_correct"] += int(status_hit)
        rows.append({
            "query_id": case["query_id"], "type": case_type, "status_expected": case["expected_status"],
            "status_actual": result.get("status"), "reason_expected": case.get("expected_reason"),
            "reason_actual": result.get("reason"), "status_correct": status_hit,
            "expected_observations": len(expected), "returned_observations": len(returned),
            "unexpected_observation_ids": sorted(unexpected), "expected_documents": len(expected_docs),
            "returned_documents": len(returned_docs), "unexpected_document_ids": sorted(unexpected_docs),
            "precision_at_5": precision if expected else None,
            "recall_at_5": recall if expected else None,
            "recall_at_5_ceiling": recall_ceiling if expected else None,
            "full_scope_recall": matched / len(expected) if expected else None,
        })
    n = len(rows)
    by_type_report = {}
    for case_type, values in sorted(by_type.items()):
        count = values["query_count"]
        by_type_report[case_type] = {
            "query_count": count,
            "status_accuracy": round(values["status_correct"] / count, 4) if count else None,
            "precision_at_5": round(sum(values["precision_values"]) / len(values["precision_values"]), 4) if values["precision_values"] else None,
            "recall_at_5": round(sum(values["recall_values"]) / len(values["recall_values"]), 4) if values["recall_values"] else None,
            "document_precision_at_5": round(sum(values["document_precision_values"]) / len(values["document_precision_values"]), 4) if values["document_precision_values"] else None,
            "document_recall_at_5": round(sum(values["document_recall_values"]) / len(values["document_recall_values"]), 4) if values["document_recall_values"] else None,
        }
    return {
        "suite_version": suite["suite_version"], "query_count": n,
        "positive_observation_query_count": len(positive_precisions),
        "status_accuracy": round(status_hits / n, 4) if n else None,
        "precision_at_5": round(sum(positive_precisions) / len(positive_precisions), 4) if positive_precisions else None,
        "recall_at_5": round(sum(positive_recalls) / len(positive_recalls), 4) if positive_recalls else None,
        "recall_at_5_macro_ceiling": round(sum(min(5, len(case.get("expected_observation_ids", []))) / len(case["expected_observation_ids"]) for case in suite["cases"] if case.get("expected_observation_ids")) / len(positive_recalls), 4) if positive_recalls else None,
        "full_scope_recall": round(sum(full_scope_recalls) / len(full_scope_recalls), 4) if full_scope_recalls else None,
        "document_precision_at_5": round(sum(document_precisions) / len(document_precisions), 4) if document_precisions else None,
        "document_recall_at_5": round(sum(document_recalls) / len(document_recalls), 4) if document_recalls else None,
        "by_query_type": by_type_report,
        "hard_negative_return_count": hard_negative_returned,
        "scope_mismatch_return_count": hard_negative_returned,
        "evidence_link_rate": round(evidence_ok / evidence_total, 4) if evidence_total else None,
        "unsupported_factual_claim_rate": "not_measured",
        "synthetic_operational_promotion_count": 0,
        "queries": rows,
        "failed_query_ids": [row["query_id"] for row in rows if not row["status_correct"] or row["unexpected_observation_ids"] or row["unexpected_document_ids"]],
        "evaluation_limitations": [
            "Synthetic fixture correctness only; not actual market accuracy or H&M representativeness.",
            "Observation retrieval is structured filtering; P@5 does not evaluate free-text market relevance.",
            "Structured observation result order is deterministic fixture order, not a relevance ranking; Recall@5 reflects the first five returned rows, while full_scope_recall measures all exact matches returned.",
            "Claim entailment and unsupported factual claim rate are not measured by this harness.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "tests/fixtures/synthetic_rag_analysis/v1/cases.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.suite)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
