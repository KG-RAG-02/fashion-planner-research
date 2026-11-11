"""Explicit, local-only RAG path for test-only synthetic fixtures.

This proposal path is deliberately independent of production Evidence and HTTP
contracts. Numeric claims come only from validated structured observations;
lexical document retrieval is limited to explanatory fixture documents.
"""

from __future__ import annotations

import hashlib
import json
import math
import calendar
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from .orchestration_request import (
    CONTRACT_VERSION,
    CanonicalRequestError,
    context_from_canonical_request,
)

SCHEMA_VERSION = "synthetic-rag-analysis/0.2-proposal"
SYNTHETIC_MARKERS = {
    "synthetic": True,
    "data_nature": "synthetic_test",
    "permission_status": "authorized_for_synthetic_testing_only",
    "operational_eligible": False,
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUERY_COVERAGE_ROOT = PROJECT_ROOT / "examples/query_coverage/generated"
SCENARIO_ROOT = PROJECT_ROOT / "examples/synthetic_market_scenario/generated"
ALLOWED_FIXTURE_ROOTS = (QUERY_COVERAGE_ROOT, SCENARIO_ROOT)
LIMITATION = (
    "합성 테스트 시나리오 자료입니다. 실제 시장 관측·예측, 수요, 매출, H&M 자료 또는 사업 실적을 뜻하지 않습니다."
)
_METRIC_ALIASES = {
    "scenario_market_value": "scenario_market_value",
    "market_estimate": "scenario_market_value",
    "market_value": "scenario_market_value",
    "scenario_units_sold": "scenario_units_sold",
    "synthetic_sales_aggregate": "scenario_units_sold",
    "purchase_rows": None,
    "revenue": None,
    "market_size": None,
    "demand": None,
    "market_share": None,
}
_SYNONYMS = {
    "니트": "knitwear", "니트웨어": "knitwear", "knit": "knitwear",
    "니트 웨어": "knitwear", "여성복": "women", "여성": "women", "women's": "women",
    "market value": "scenario_market_value", "시장 가치": "scenario_market_value",
    "시장규모": "market_size", "시장 규모": "market_size", "수요": "demand",
    "매출": "revenue", "판매량": "scenario_units_sold", "units sold": "scenario_units_sold",
}


class SyntheticFixtureError(ValueError):
    """Fixture is malformed, untrusted, or outside the explicit local allowlist."""


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticFixtureError(f"fixture file is missing or invalid: {path.name}") from exc


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SyntheticFixtureError(f"fixture shard is missing: {path.name}") from exc
    rows = []
    try:
        for line in lines:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise SyntheticFixtureError(f"fixture row is not an object: {path.name}")
                rows.append(row)
    except json.JSONDecodeError as exc:
        raise SyntheticFixtureError(f"fixture shard is invalid: {path.name}") from exc
    return rows


def _markers(value: Mapping[str, Any], label: str) -> None:
    if any(value.get(key) != expected for key, expected in SYNTHETIC_MARKERS.items()):
        raise SyntheticFixtureError(f"synthetic safety marker mismatch: {label}")


def _safe_root(root: Path, trusted_external_roots: tuple[Path, ...] = ()) -> Path:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise SyntheticFixtureError("fixture root does not exist or cannot be resolved") from exc
    allowed = {path.resolve(strict=True) for path in ALLOWED_FIXTURE_ROOTS if path.exists()}
    for trusted in trusted_external_roots:
        try:
            allowed.add(Path(trusted).resolve(strict=True))
        except OSError:
            continue
    if resolved not in allowed or not resolved.is_dir():
        raise SyntheticFixtureError("fixture root is not an allowlisted local synthetic corpus")
    return resolved


def load_fixture_bundle(
    query_coverage_root: Path = QUERY_COVERAGE_ROOT,
    scenario_root: Path = SCENARIO_ROOT,
    *,
    trusted_external_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Read and validate both allowlisted corpora without converting permissions."""
    coverage_root = _safe_root(Path(query_coverage_root), trusted_external_roots)
    docs_root = _safe_root(Path(scenario_root), trusted_external_roots)
    manifest = _json(coverage_root / "manifest.json")
    if manifest.get("schema_version") != "synthetic-query-coverage-manifest/1.1":
        raise SyntheticFixtureError("unsupported query coverage manifest version")
    _markers(manifest, "query coverage manifest")
    scenario_manifest = _json(docs_root / "manifest.json")
    _markers(scenario_manifest, "scenario manifest")
    if scenario_manifest.get("empirical_calibration") is not False:
        raise SyntheticFixtureError("scenario corpus must declare empirical_calibration=false")

    observation_sets: dict[str, list[dict[str, Any]]] = {"market_like": [], "sales_like": []}
    evidence_sets: dict[str, dict[str, dict[str, Any]]] = {"market_like": {}, "sales_like": {}}
    for domain, key, expected_source in (
        ("market_like", "market_observations", "synthetic-generator-query-coverage"),
        ("sales_like", "synthetic_sales_observations", "synthetic-generator-query-coverage-sales"),
    ):
        shard_specs = manifest.get("shards", {}).get(key)
        if not isinstance(shard_specs, list) or not shard_specs:
            raise SyntheticFixtureError(f"manifest lacks {key} shards")
        for shard in shard_specs:
            rel = Path(str(shard.get("path", "")))
            if rel.is_absolute() or ".." in rel.parts:
                raise SyntheticFixtureError("manifest contains unsafe shard path")
            rows = _jsonl(coverage_root / rel)
            if len(rows) != shard.get("record_count"):
                raise SyntheticFixtureError(f"record count mismatch: {rel.name}")
            for row in rows:
                _markers(row, row.get("observation_id", "unknown observation"))
                if row.get("source_id") != expected_source:
                    raise SyntheticFixtureError(f"source/domain mismatch: {row.get('observation_id')}")
                if not isinstance(row.get("value"), (int, float)) or isinstance(row.get("value"), bool) or not math.isfinite(float(row["value"])):
                    raise SyntheticFixtureError("observation value must be numeric")
                if not row.get("observation_id") or not row.get("evidence_ids") or len(set(row["evidence_ids"])) != len(row["evidence_ids"]):
                    raise SyntheticFixtureError("observation is missing ID or Evidence lineage")
                try:
                    period_start = date.fromisoformat(row["period_start"])
                    period_end = date.fromisoformat(row["period_end"])
                except (KeyError, TypeError, ValueError):
                    raise SyntheticFixtureError("observation period is missing or invalid") from None
                if period_start > period_end:
                    raise SyntheticFixtureError("observation period is inverted")
                if row.get("period_grain") == "month":
                    month_end_day = calendar.monthrange(period_start.year, period_start.month)[1]
                    if period_start.day != 1 or period_end.year != period_start.year or period_end.month != period_start.month or period_end.day != month_end_day:
                        raise SyntheticFixtureError("monthly observation does not span a complete calendar month")
                measure = manifest["coverage"]["market_measure" if domain == "market_like" else "sales_measure"]
                if any(row.get(key) != measure.get(source_key, expected) for key, source_key, expected in (
                    ("metric_name", "metric_name", None), ("unit", "unit", None), ("measure_type", "measure_type", None)
                )):
                    raise SyntheticFixtureError(f"observation metric/unit contract mismatch: {row['observation_id']}")
                observation_sets[domain].append(row)
        evidence_key = "evidence"
        for shard in manifest.get("shards", {}).get(evidence_key, []):
            rel = Path(str(shard.get("path", "")))
            evidence_rows = _jsonl(coverage_root / rel)
            if len(evidence_rows) != shard.get("record_count"):
                raise SyntheticFixtureError(f"evidence count mismatch: {rel.name}")
            for evidence in evidence_rows:
                _markers(evidence, evidence.get("evidence_id", "unknown evidence"))
                if evidence.get("source_id") != expected_source:
                    continue
                evidence_id = evidence.get("evidence_id")
                if not evidence_id or evidence_id in evidence_sets[domain]:
                    raise SyntheticFixtureError("duplicate or missing Evidence ID")
                evidence_sets[domain][evidence_id] = evidence
        for row in observation_sets[domain]:
            if any(eid not in evidence_sets[domain] for eid in row["evidence_ids"]):
                raise SyntheticFixtureError(f"observation Evidence link is invalid: {row['observation_id']}")
            for eid in row["evidence_ids"]:
                evidence = evidence_sets[domain][eid]
                for key in ("source_id", "period_start", "period_end"):
                    if evidence.get(key) != row.get(key):
                        raise SyntheticFixtureError(f"observation Evidence scope mismatch: {row['observation_id']}")
                if (("mapping_id" in evidence and evidence.get("mapping_id") != row.get("product_mapping_id"))
                        or ("mapping_version" in evidence and evidence.get("mapping_version") != row.get("product_mapping_version"))):
                    raise SyntheticFixtureError(f"observation Evidence mapping mismatch: {row['observation_id']}")
                for key in ("category", "gender", "geography", "channel"):
                    if key in evidence and evidence.get(key) != row.get(key):
                        raise SyntheticFixtureError(f"observation Evidence scope mismatch: {row['observation_id']}")
        for evidence_id, evidence in evidence_sets[domain].items():
            linked_rows = sorted(
                (row for row in observation_sets[domain] if evidence_id in row["evidence_ids"]),
                key=lambda item: item["observation_id"],
            )
            canonical_rows = json.dumps(linked_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(canonical_rows.encode("utf-8")).hexdigest()
            if not linked_rows or evidence.get("content_hash") != expected_hash:
                raise SyntheticFixtureError(f"observation Evidence content hash mismatch: {evidence_id}")

    for domain in observation_sets:
        ids = [row["observation_id"] for row in observation_sets[domain]]
        if len(ids) != len(set(ids)):
            raise SyntheticFixtureError(f"duplicate observation ID in {domain}")
        declared_grains = set()
        for row in observation_sets[domain]:
            grain = tuple(json.dumps(row.get(key), ensure_ascii=False, sort_keys=True) for key in (
                "source_id", "metric_name", "metric_definition", "measure_type", "unit", "currency",
                "period_grain", "period_start", "period_end", "geography", "channel", "gender",
                "category", "season", "season_year", "population", "product_mapping_id",
                "product_mapping_version", "attributes", "vintage_id",
            ))
            if grain in declared_grains:
                raise SyntheticFixtureError(f"duplicate observation grain in {domain}")
            declared_grains.add(grain)

    documents = _jsonl(docs_root / "documents.jsonl")
    evidence_data = _json(docs_root / "evidence.json")
    if not isinstance(evidence_data, list) or len(documents) != scenario_manifest.get("document_count"):
        raise SyntheticFixtureError("scenario document/evidence registry count mismatch")
    doc_evidence: dict[str, dict[str, Any]] = {}
    for item in evidence_data:
        _markers(item, item.get("evidence_id", "unknown evidence"))
        evidence_id = item.get("evidence_id")
        if not evidence_id or evidence_id in doc_evidence:
            raise SyntheticFixtureError("duplicate or missing scenario Evidence ID")
        doc_evidence[evidence_id] = item
    for doc in documents:
        _markers(doc, doc.get("document_id", "unknown document"))
        evidence = doc_evidence.get(doc.get("evidence_id"))
        if not evidence:
            raise SyntheticFixtureError(f"document Evidence link is invalid: {doc.get('document_id')}")
        digest = hashlib.sha256(str(doc.get("text", "")).encode("utf-8")).hexdigest()
        if digest != doc.get("content_hash") or evidence.get("content_hash") != digest:
            raise SyntheticFixtureError(f"document content hash mismatch: {doc.get('document_id')}")
    if len(observation_sets["market_like"]) != manifest.get("record_counts", {}).get("market_observations"):
        raise SyntheticFixtureError("market observation total count mismatch")
    if len(observation_sets["sales_like"]) != manifest.get("record_counts", {}).get("synthetic_sales_observations"):
        raise SyntheticFixtureError("sales observation total count mismatch")
    return {
        "manifest": manifest,
        "scenario_manifest": scenario_manifest,
        "observations": observation_sets,
        "evidence": evidence_sets,
        "documents": documents,
        "document_evidence": doc_evidence,
        "roots": {"observations": str(coverage_root), "documents": str(docs_root)},
    }


def _normalized_terms(text: str) -> set[str]:
    normalized = text.casefold()
    for term, replacement in sorted(_SYNONYMS.items(), key=lambda item: -len(item[0])):
        normalized = normalized.replace(term.casefold(), replacement)
    return set(re.findall(r"[a-z0-9_]+|[가-힣]+", normalized))


def _period_values(canonical: Mapping[str, Any]) -> tuple[date | None, date | None, bool]:
    scope = canonical["scope"]
    period = scope["period"]
    if period["certainty"] != "resolved":
        return None, None, False
    return date.fromisoformat(period["start"]), date.fromisoformat(period["end"]), True


def _scope_value(scope: Mapping[str, Any], key: str) -> tuple[str | None, str | None]:
    item = scope.get(key)
    if item is None:
        return None, None
    return str(item["value"]), str(item["certainty"])


def _supported_metric(raw: str) -> str | None:
    key = raw.casefold().strip().replace(" ", "_")
    return _METRIC_ALIASES.get(key)


def _requested_domain(canonical: Mapping[str, Any]) -> tuple[str | None, str | None]:
    intent = canonical["intent"]["primary"]
    metrics = canonical["scope"]["metrics"]
    if intent == "definition_lookup":
        return "documents", None
    if intent not in {"market_observation", "sales_observation"}:
        return None, "unsupported_intent"
    if len(metrics) != 1:
        return None, "metric_must_be_explicit_and_unambiguous"
    metric = _supported_metric(metrics[0])
    if metric is None:
        return None, "unsupported_metric"
    if metric == "scenario_market_value" and intent != "market_observation":
        return None, "metric_intent_mismatch"
    if metric == "scenario_units_sold" and intent != "sales_observation":
        return None, "metric_intent_mismatch"
    return ("market_like" if metric == "scenario_market_value" else "sales_like"), None


def _matches(row: Mapping[str, Any], canonical: Mapping[str, Any]) -> tuple[bool, str | None]:
    scope = canonical["scope"]
    for key in ("category", "gender", "season", "geography", "channel"):
        value, certainty = _scope_value(scope, key)
        if certainty != "explicit":
            continue
        if key == "season" and value is not None:
            value = {"spring": "SS", "summer": "SS", "fall": "FW", "autumn": "FW", "winter": "FW"}.get(value.casefold(), value.upper())
        if value is not None and str(row.get(key, "")).casefold() != value.casefold():
            return False, f"scope_mismatch:{key}"
    metrics = scope["metrics"]
    if metrics and _supported_metric(metrics[0]) != row.get("metric_name"):
        return False, "scope_mismatch:metric"
    start, end, resolved = _period_values(canonical)
    if resolved:
        row_start = date.fromisoformat(row["period_start"])
        row_end = date.fromisoformat(row["period_end"])
        if row_start > end or row_end < start:
            return False, "scope_mismatch:period"
    return True, None


def _format_monthly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = defaultdict(float)
    obs: dict[str, list[str]] = defaultdict(list)
    evs: dict[str, list[str]] = defaultdict(list)
    dimensions: dict[str, dict[str, Any]] = {}
    group_fields = (
        "source_id", "metric_name", "metric_definition", "measure_type", "unit", "currency", "geography", "channel", "gender",
        "category", "season", "season_year", "population", "product_mapping_id",
        "product_mapping_version", "attributes",
    )
    for row in rows:
        month = row["period_start"][:7]
        group = {key: row.get(key) for key in group_fields}
        group_key = json.dumps([month, group], ensure_ascii=False, sort_keys=True)
        totals[group_key] += float(row["value"])
        obs[group_key].append(row["observation_id"])
        evs[group_key].extend(row["evidence_ids"])
        dimensions[group_key] = group
    return [
        {"period": json.loads(group_key)[0], "dimensions": dimensions[group_key],
         "value": round(totals[group_key], 2), "observation_ids": sorted(obs[group_key]),
         "evidence_ids": sorted(set(evs[group_key]))}
        for group_key in sorted(totals)
    ]


def _format_calculations(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Calculate adjacent-month percent changes only within identical synthetic series."""
    dimensions_fields = (
        "source_id", "metric_name", "metric_definition", "measure_type", "unit", "currency",
        "period_grain", "geography", "channel", "gender", "category", "season", "season_year",
        "population", "product_mapping_id", "product_mapping_version", "attributes", "vintage_id",
    )
    groups: dict[str, dict[str, Any]] = {}
    issues: Counter[str] = Counter()
    for row in rows:
        if row.get("period_grain") != "month":
            issues["unsupported_period_grain"] += 1
            continue
        dimensions = {key: row.get(key) for key in dimensions_fields}
        group_key = json.dumps(dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        group = groups.setdefault(group_key, {"dimensions": dimensions, "months": defaultdict(list)})
        group["months"][row["period_start"][:7]].append(row)

    calculations: list[dict[str, Any]] = []
    eligible_series = 0
    for group_key, group in sorted(groups.items()):
        dimensions = group["dimensions"]
        if dimensions["metric_name"] not in {"scenario_market_value", "scenario_units_sold"}:
            issues["unsupported_metric"] += 1
            continue
        month_rows = sorted(group["months"].items())
        if len(month_rows) < 2:
            issues["single_period"] += 1
            continue
        eligible_series += 1
        for (baseline_month, baseline_rows), (current_month, current_rows) in zip(month_rows, month_rows[1:]):
            if len(baseline_rows) != 1 or len(current_rows) != 1:
                issues["duplicate_grain"] += 1
                continue
            year, month = (int(part) for part in baseline_month.split("-"))
            next_month = f"{year + 1:04d}-01" if month == 12 else f"{year:04d}-{month + 1:02d}"
            if current_month != next_month:
                issues["period_gap"] += 1
                continue
            baseline = baseline_rows[0]
            current = current_rows[0]
            try:
                if date.fromisoformat(current["period_start"]) != date.fromisoformat(baseline["period_end"]) + timedelta(days=1):
                    issues["period_boundary_mismatch"] += 1
                    continue
            except (KeyError, ValueError):
                issues["period_boundary_incomplete"] += 1
                continue
            baseline_value = float(baseline["value"])
            if baseline_value == 0:
                issues["zero_baseline"] += 1
                continue
            current_value = float(current["value"])
            observation_ids = [baseline["observation_id"], current["observation_id"]]
            evidence_ids = sorted(set((*baseline["evidence_ids"], *current["evidence_ids"])))
            identity = "|".join((*observation_ids, *evidence_ids))
            calculations.append({
                "calculation_id": "synthetic-change-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                "metric_name": f"{dimensions['metric_name']}_relative_change_percent",
                "value": round((current_value / baseline_value - 1) * 100, 6),
                "unit": "percent",
                "formula_id": "adjacent_month_relative_change_percent",
                "formula_version": "synthetic-analysis/1.0",
                "formula": "(current_value / baseline_value - 1) * 100",
                "baseline_period": baseline_month,
                "current_period": current_month,
                "baseline_value": baseline_value,
                "current_value": current_value,
                "input_unit": dimensions["unit"],
                "input_observation_ids": observation_ids,
                "evidence_ids": evidence_ids,
                "dimensions": dimensions,
                **SYNTHETIC_MARKERS,
            })
    limitations = []
    if eligible_series and not calculations:
        limitations.append("비교 가능한 연속 월 구간이 없어 변화율을 계산하지 않았습니다.")
    for issue, count in sorted(issues.items()):
        limitations.append(f"합성 시나리오 변화율 계산 {count}건을 차단했습니다: {issue}.")
    return calculations, limitations


def _serialize_observation(row: Mapping[str, Any], evidence_registry: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    result = {key: row.get(key) for key in (
        "observation_id", "source_id", "source_schema_version", "request_id", "metric_name", "metric_definition",
        "measure_type", "value", "unit", "currency", "period_start", "period_end", "period_grain",
        "geography", "channel", "gender", "category", "season", "season_year", "population",
        "product_mapping_id", "product_mapping_version", "attributes", "vintage_id",
    )}
    result.update(SYNTHETIC_MARKERS)
    result["evidence_ids"] = list(row["evidence_ids"])
    result["evidence"] = [
        {key: evidence_registry[eid].get(key) for key in (
            "evidence_id", "source_id", "source_type", "source_name", "locator", "content_hash",
            "period_start", "period_end", "mapping_id", "mapping_version", "permission_status",
            "synthetic", "data_nature", "operational_eligible",
        )}
        for eid in row["evidence_ids"]
    ]
    return result


def _rank_documents(documents: list[dict[str, Any]], raw_query: str) -> list[dict[str, Any]]:
    terms = _normalized_terms(raw_query)
    if not terms:
        return []
    ranked = []
    seen: set[tuple[str, str]] = set()
    for doc in documents:
        doc_terms = _normalized_terms(str(doc.get("text", "")) + " " + str(doc.get("source_name", "")))
        overlap = len(terms & doc_terms)
        if not overlap:
            continue
        key = (str(doc.get("document_id")), str(doc.get("evidence_id")))
        if key in seen:
            continue
        seen.add(key)
        ranked.append((overlap / len(terms), doc))
    ranked.sort(key=lambda item: (-item[0], item[1]["document_id"]))
    return [{"document_id": doc["document_id"], "document_version": doc.get("document_version"),
             "source_id": doc.get("source_id"), "source_name": doc.get("source_name"), "source_type": doc.get("source_type"),
             "evidence_id": doc["evidence_id"], **SYNTHETIC_MARKERS,
             "score": round(score, 6), "score_meaning": "lexical relevance rank only",
             "locator": doc["locator"], "content_hash": doc["content_hash"], "text": doc["text"]}
            for score, doc in ranked[:5]]


def _document_candidates(documents: list[dict[str, Any]], canonical: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    excluded: Counter[str] = Counter()
    candidates = []
    for doc in documents:
        mismatch = None
        for key in ("geography", "channel"):
            value, certainty = _scope_value(canonical["scope"], key)
            if certainty != "explicit":
                continue
            if value and str(doc.get(key, "")).casefold() != value.casefold():
                mismatch = f"scope_mismatch:{key}"
                break
        category, certainty = _scope_value(canonical["scope"], "category")
        if not mismatch and category and certainty == "explicit" and str(doc.get("category", "")).casefold() not in {category.casefold(), "all"}:
            mismatch = "scope_mismatch:category"
        start, end, has_period = _period_values(canonical)
        if not mismatch and has_period:
            if not doc.get("period_start") or not isinstance(doc.get("period_months"), int) or doc["period_months"] < 1:
                mismatch = "scope_mismatch:period_unavailable"
            else:
                year, month = (int(part) for part in str(doc["period_start"]).split("-")[:2])
                doc_start = date(year, month, 1)
                end_index = year * 12 + month - 1 + doc["period_months"] - 1
                doc_end = date(end_index // 12, end_index % 12 + 1, 1)
                if doc_start > end or doc_end < start:
                    mismatch = "scope_mismatch:period"
        if mismatch:
            excluded[mismatch] += 1
        else:
            candidates.append(doc)
    return candidates, dict(sorted(excluded.items()))


def analyze_synthetic_request(
    request: Mapping[str, Any],
    *,
    mode: str,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a canonical structured request against validated test fixtures."""
    if mode != "synthetic_test":
        raise SyntheticFixtureError("execution requires explicit --mode synthetic_test")
    context = context_from_canonical_request(request)
    canonical = context.canonical_request or dict(request)
    if context.query_ambiguities:
        return _base_result(canonical, "no_results", "ambiguous_request", bundle)
    data = bundle or load_fixture_bundle()
    domain, reason = _requested_domain(canonical)
    if domain is None:
        status = "no_results" if reason in {"unsupported_intent", "unsupported_metric", "metric_intent_mismatch", "metric_must_be_explicit_and_unambiguous"} else "error"
        result = _base_result(canonical, status, reason or "unsupported_request", data)
        result["analysis"]["summary"] = "요청된 분석은 이 합성 fixture의 정의와 지표 범위에서 지원되지 않습니다."
        return result

    if domain == "documents":
        candidates, excluded = _document_candidates(data["documents"], canonical)
        hits = _rank_documents(candidates, canonical["raw_query"])
        for hit in hits:
            evidence = data["document_evidence"].get(hit["evidence_id"])
            if not evidence or evidence.get("content_hash") != hit["content_hash"]:
                raise SyntheticFixtureError("document retrieval returned invalid Evidence lineage")
        status = "ok" if hits else "no_results"
        result = _base_result(canonical, status, None if hits else "no_matching_documents", data)
        result["retrieval"].update({"domain": "explanatory_documents", "document_hits": hits,
                                    "status": status,
                                    "candidate_count": len(data["documents"]), "matched_count": len(hits),
                                    "excluded_counts": excluded,
                                    "applied_filters": ["synthetic markers", "explicit geography/channel/category", "document Evidence lineage"]})
        result["analysis"]["summary"] = "합성 설명 문서에서 관련 방법론 근거를 찾았습니다." if hits else "일치하는 합성 설명 문서가 없습니다."
        result["analysis"]["findings"] = [
            {"kind": "methodology_reference", "document_id": hit["document_id"],
             "evidence_ids": [hit["evidence_id"]], "statement": hit["text"]}
            for hit in hits
        ]
        return result

    rows = data["observations"][domain]
    matched = []
    excluded: Counter[str] = Counter()
    for row in rows:
        ok, why = _matches(row, canonical)
        if ok:
            matched.append(row)
        elif why:
            excluded[why] += 1
    measure = data["manifest"]["coverage"]["market_measure" if domain == "market_like" else "sales_measure"]
    start, end, has_period = _period_values(canonical)
    missing_explicit = []
    for key in ("category", "gender"):
        value, certainty = _scope_value(canonical["scope"], key)
        if value is None:
            missing_explicit.append(key)
    if not has_period:
        missing_explicit.append("period")
    inferred_not_filtered = {
        key: value["value"] for key, value in canonical["scope"].items()
        if isinstance(value, dict) and value.get("certainty") == "inferred"
    }
    status = "ok" if matched else "no_results"
    result = _base_result(canonical, status, None if matched else "no_matching_observations", data)
    block = {
        "status": status,
        **SYNTHETIC_MARKERS,
        "source_id": measure.get("source_id", "synthetic-generator-query-coverage" if domain == "market_like" else "synthetic-generator-query-coverage-sales"),
        "manifest_id": data["manifest"].get("request_id"),
        "metric_name": measure["metric_name"],
        "measure_type": measure["measure_type"],
        "unit": measure["unit"],
        "currency": measure.get("currency"),
        "observations": [_serialize_observation(row, data["evidence"][domain]) for row in matched],
        "evidence_ids": sorted({eid for row in matched for eid in row["evidence_ids"]}),
        "monthly_aggregates": _format_monthly(matched),
        "calculations": [],
        "calculation_limitations": [],
        "limitations": [LIMITATION, "집계는 일치 observation의 단순 합계이며 누락 기간을 0으로 채우지 않습니다."],
    }
    calculations, calculation_limitations = _format_calculations(matched)
    block["calculations"] = calculations
    block["calculation_limitations"] = calculation_limitations
    block["limitations"].extend(calculation_limitations)
    result["limitations"].extend(calculation_limitations)
    result["market_like" if domain == "market_like" else "sales_like"] = block
    result["retrieval"].update({
        "status": status,
        "domain": domain,
        "applied_filters": ["synthetic markers", "intent/metric compatibility", "explicit scope exact match", "resolved period overlap", "Evidence lineage"],
        "candidate_count": len(rows), "matched_count": len(matched),
        "excluded_counts": dict(sorted(excluded.items())),
        "scope_limitations": [f"scope_not_specified:{field}" for field in missing_explicit],
        "inferred_scope_not_used_as_hard_filter": inferred_not_filtered,
        "returned_observation_ids": [row["observation_id"] for row in matched],
        "returned_evidence_ids": sorted({eid for row in matched for eid in row["evidence_ids"]}),
    })
    if matched:
        result["analysis"]["summary"] = (
            f"합성 {measure['metric_name']} 시나리오에서 {len(matched)}개 관측 행을 찾았습니다. "
            "집계값은 해당 합성 fixture 범위에만 적용됩니다."
        )
        result["analysis"]["findings"] = [
            {"kind": "synthetic_fixture_aggregate", "calculation_id": "synthetic-sum-" + hashlib.sha256("|".join(item["observation_ids"]).encode("utf-8")).hexdigest()[:16],
             "formula_id": "sum_compatible_observations", "formula_version": "1.0-proposal",
             "period": item["period"], "dimensions": item["dimensions"], "value": item["value"],
             "unit": measure["unit"], "observation_ids": item["observation_ids"], "evidence_ids": item["evidence_ids"]}
            for item in block["monthly_aggregates"]
        ]
        result["analysis"]["findings"].extend(
            {"kind": "synthetic_fixture_calculation", "calculation_id": item["calculation_id"],
             "statement": (f"[SYNTHETIC TEST DATA] {item['metric_name']} changed {item['value']:g}% from "
                           f"{item['baseline_period']} to {item['current_period']} using {item['formula']}. "
                           "This describes only the synthetic scenario and is not a market trend or business result."),
             "value": item["value"], "unit": item["unit"], "input_observation_ids": item["input_observation_ids"],
             "evidence_ids": item["evidence_ids"]}
            for item in calculations
        )
    return result


def _base_result(canonical: Mapping[str, Any], status: str, reason: str | None, bundle: dict[str, Any] | None) -> dict[str, Any]:
    manifest = bundle["manifest"] if bundle else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": canonical.get("request_id"),
        "execution_mode": "synthetic_test",
        **SYNTHETIC_MARKERS,
        "requested_scope": {
            "raw_query": canonical.get("raw_query"),
            "explicit": {key: item["value"] for key, item in canonical["scope"].items()
                          if isinstance(item, dict) and item.get("certainty") == "explicit" and "value" in item},
            "inferred": {key: item["value"] for key, item in canonical["scope"].items()
                         if isinstance(item, dict) and item.get("certainty") == "inferred" and "value" in item},
            "period": canonical["scope"]["period"],
            "metrics": list(canonical["scope"]["metrics"]),
            "ambiguities": list(canonical["scope"]["ambiguities"]),
            "intent": canonical["intent"]["primary"],
            "parser_version": f"{canonical['query_parser']['name']}/{canonical['query_parser']['version']}",
        },
        "status": status,
        "reason": reason,
        "source_manifests": {
            "observations": {key: manifest.get(key) for key in ("schema_version", "generator_version", "seed", "request_id")},
            "explanatory_documents": ({key: bundle["scenario_manifest"].get(key) for key in ("manifest_id", "generator", "seed", "request_id")} if bundle else None),
            "r2_release": bundle.get("_r2_release") if bundle else None,
        },
        "market_like": {"status": "not_queried", "unit": None, "observations": [], "evidence_ids": []},
        "sales_like": {"status": "not_queried", "unit": None, "observations": [], "evidence_ids": []},
        "retrieval": {"status": "not_run", "applied_filters": [], "document_hits": [], "excluded_counts": {}},
        "analysis": {"status": status, "summary": "", "findings": []},
        "comparability": {"status": "not_comparable", "reasons": ["시장 유사와 판매 유사 fixture는 별도 지표·단위·모집단입니다."]},
        "limitations": [LIMITATION],
    }
