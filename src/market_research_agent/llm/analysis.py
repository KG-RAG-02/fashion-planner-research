"""Explicit two-domain LLM analysis using the versioned local prompts.

LLM output is narrative analysis only. Source facts and deterministic values are
prepared locally, and every returned finding must cite a supplied Evidence ID.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .responses_api import LlmCallError, OpenAIResponsesClient


ROOT = Path(__file__).resolve().parents[3]
PROMPT_ROOT = ROOT / "prompts"
DEFAULT_HM_DIR = ROOT / "data/processed/hm-personalized-fashion-recommendations"
DEFAULT_MARKET_DIR = ROOT / "examples/synthetic_market_scenario/generated"
TEST_ONLY_PERMISSION = "authorized_for_synthetic_testing_only"
LLMFunction = Callable[..., dict[str, Any]]


class AnalysisInputError(ValueError):
    """Unsafe or incomplete input, rejected before an LLM request."""


def _load_prompt(domain: str) -> tuple[str, str, str]:
    if domain not in {"sales_analysis", "market_analysis"}:
        raise AnalysisInputError("Unknown analysis prompt.")
    manifest = json.loads((PROMPT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["prompts"][domain]["versions"]["v1"]
    system = (PROMPT_ROOT / entry["system_template"]).read_text(encoding="utf-8")
    user_template = (PROMPT_ROOT / entry["user_template"]).read_text(encoding="utf-8")
    if "{{ANALYSIS_INPUT_JSON}}" not in user_template:
        raise AnalysisInputError("Prompt input placeholder is missing.")
    contract = (PROMPT_ROOT / "contracts/analysis-output-v0.1.json").read_text(encoding="utf-8")
    system += (
        "\n\nOUTPUT CONTRACT\nReturn one JSON object matching this schema exactly. "
        "Do not add markdown fences. Each factual finding needs supplied Evidence IDs. "
        "The summary may only restate cited findings and must not add new facts or numbers. "
        "Do not infer calculations; explain calculations only when already present in input.\n" + contract
    )
    return system, user_template, entry["system_template"]


def _format_user_prompt(template: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    return template.replace("{{ANALYSIS_INPUT_JSON}}", encoded)


def _validate_output(
    output: dict[str, Any], *, domain: str, request_id: str, allowed_evidence_ids: set[str],
) -> dict[str, Any]:
    required = {"status", "summary", "findings", "limitations", "missing_data"}
    if not required.issubset(output):
        raise LlmCallError("LLM output is missing required analysis fields.")
    if output["status"] not in {"ok", "partial", "no_results", "error"}:
        raise LlmCallError("LLM output has an unsupported status.")
    if not isinstance(output["summary"], str) or not isinstance(output["findings"], list):
        raise LlmCallError("LLM output has invalid analysis field types.")
    if not isinstance(output["limitations"], list) or not isinstance(output["missing_data"], list):
        raise LlmCallError("LLM output has invalid limitation field types.")
    for item in (*output["limitations"], *output["missing_data"]):
        if not isinstance(item, str):
            raise LlmCallError("LLM output limitation entries must be strings.")
    for finding in output["findings"]:
        if not isinstance(finding, dict) or not isinstance(finding.get("statement"), str):
            raise LlmCallError("LLM output contains an invalid finding.")
        evidence_ids = finding.get("evidence_ids")
        if not isinstance(evidence_ids, list) or any(not isinstance(item, str) for item in evidence_ids):
            raise LlmCallError("Each finding must include an evidence_ids array.")
        if not set(evidence_ids).issubset(allowed_evidence_ids):
            raise LlmCallError("LLM output cited an Evidence ID that was not supplied.")
        if finding.get("kind", "fact") in {"fact", "interpretation"} and not evidence_ids:
            raise LlmCallError("Factual findings must cite supplied Evidence IDs.")

    return {
        "schema_version": f"{domain.replace('_', '-')}-llm/0.1-proposal",
        "request_id": request_id,
        "agent_id": "sales-analysis-agent" if domain == "sales_analysis" else "market-analysis-agent",
        "synthetic": True if domain == "market_analysis" else False,
        "data_nature": "synthetic_test" if domain == "market_analysis" else "historical_kaggle_observations",
        "operational_eligible": False,
        "permission_status": (
            TEST_ONLY_PERMISSION if domain == "market_analysis"
            else "caller_asserted_external_permission_reference"
        ),
        **output,
    }


def _call_domain(
    *, domain: str, payload: dict[str, Any], allowed_evidence_ids: set[str],
    client: OpenAIResponsesClient | None = None, call: LLMFunction | None = None,
) -> dict[str, Any]:
    request_id = str(payload.get("request_id", ""))
    if not request_id:
        raise AnalysisInputError("request_id is required.")
    system, user_template, prompt_path = _load_prompt(domain)
    input_text = _format_user_prompt(user_template, payload)
    if call is not None:
        response = call(instructions=system, input_text=input_text)
    else:
        llm = client or OpenAIResponsesClient()
        response = llm.create_json_response(instructions=system, input_text=input_text)
    validated = _validate_output(
        response["output"], domain=domain, request_id=request_id,
        allowed_evidence_ids=allowed_evidence_ids,
    )
    return {
        "analysis": validated,
        "llm": {
            "provider": "openai_responses_api",
            "model": (client.model if client else None),
            "response_id": response.get("response_id"),
            "prompt_id": domain,
            "prompt_version": "v1",
            "prompt_status": "proposal",
            "system_prompt_path": prompt_path,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "evidence_ids_supplied": sorted(allowed_evidence_ids),
    }


def _require_hm_permission(permission_reference: str | None) -> str:
    if not permission_reference or not permission_reference.strip():
        raise AnalysisInputError(
            "H&M data are not loaded. Supply a verified permission reference and explicit --hm-usage-authorized acknowledgement; "
            "the acknowledgement itself does not establish permission."
        )
    return permission_reference.strip()


def load_hm_sales_context(
    *, permission_reference: str | None, usage_acknowledged: bool,
    processed_dir: Path = DEFAULT_HM_DIR,
) -> tuple[dict[str, Any], set[str]]:
    if not usage_acknowledged:
        raise AnalysisInputError(
            "H&M data are permission-gated and were not read. Pass --hm-usage-authorized only after verifying allowed use and external LLM transmission."
        )
    reference = _require_hm_permission(permission_reference)
    source_path = processed_dir / "hm_analysis.json"
    summary = json.loads(source_path.read_text(encoding="utf-8"))
    evidence_ids: set[str] = set()
    top_styles = []
    for row in summary.get("top_styles", [])[:10]:
        sales_evidence = row.get("sales_evidence_id")
        catalog_evidence = row.get("catalog_evidence_id")
        for evidence_id in (sales_evidence, catalog_evidence):
            if isinstance(evidence_id, str) and evidence_id:
                evidence_ids.add(evidence_id)
        # Omit article/product IDs and names before any third-party transmission.
        top_styles.append({
            "product_group_name": row.get("product_group_name"),
            "product_type_name": row.get("product_type_name"),
            "colour_group_name": row.get("representative_colour_group_name"),
            "purchase_rows": row.get("purchase_rows"),
            "price_mean_source_units": row.get("price_mean_source_units"),
            "top_sales_channel_id": row.get("top_sales_channel_id"),
            "observation_period": row.get("observation_period"),
            "evidence_ids": [item for item in (sales_evidence, catalog_evidence) if item],
        })
    source_ids = summary.get("source_ids", {})
    payload = {
        "request_id": f"hm-history-{summary.get('analysis_version', 'unknown')}",
        "data_nature": "historical_kaggle_observations",
        "synthetic": False,
        "dataset": summary.get("dataset"),
        "observation_period": summary.get("observation_period"),
        "measure_definition": "purchase_rows are transaction-row counts; not units, revenue, current sales, or demand.",
        "aggregation_note": "Only top-style rows below include row-level Evidence IDs; do not infer totals or rankings outside this supplied subset.",
        "top_styles": top_styles,
        "limitations": summary.get("limitations", []),
        "source_ids": source_ids,
    }
    # Keep the review reference local; never send approval metadata to the model.
    _ = reference
    return payload, evidence_ids


def retrieve_market_context(
    *, query: str, request_id: str, market_dir: Path = DEFAULT_MARKET_DIR,
    category: str | None = None, metric: str | None = None,
) -> tuple[dict[str, Any], set[str]]:
    root = market_dir.resolve()
    manifest_path = root / "manifest.json"
    observations_path = root / "market_observations.csv"
    documents_path = root / "documents.jsonl"
    if not all(path.is_file() for path in (manifest_path, observations_path, documents_path)):
        raise AnalysisInputError("Synthetic market fixture is incomplete; generate it before use.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("synthetic") is not True or manifest.get("data_nature") != "synthetic_test"
            or manifest.get("permission_status") != TEST_ONLY_PERMISSION
            or manifest.get("operational_eligible") is not False):
        raise AnalysisInputError("Market fixture lacks required test-only safety markers.")

    observations: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    with observations_path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("synthetic", "").casefold() != "true" or row.get("data_nature") != "synthetic_test":
                raise AnalysisInputError("Market observation is missing its synthetic marker.")
            if row.get("permission_status") != TEST_ONLY_PERMISSION:
                raise AnalysisInputError("Market observation has an invalid test permission marker.")
            if category and category.casefold() not in row.get("category", "").casefold():
                continue
            if metric and row.get("metric_name") != metric:
                continue
            for field in ("value", "seed"):
                if row.get(field):
                    row[field] = float(row[field]) if field == "value" else int(row[field])
            row_evidence = [part.strip() for part in row.get("evidence_ids", "").split(",") if part.strip()]
            evidence_ids.update(row_evidence)
            row["evidence_ids"] = row_evidence
            observations.append(row)

    documents = []
    query_terms = set(re.findall(r"[\w가-힣]+", query.casefold()))
    for line in documents_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        document = json.loads(line)
        if (document.get("synthetic") is not True or document.get("data_nature") != "synthetic_test"
                or document.get("permission_status") != TEST_ONLY_PERMISSION):
            raise AnalysisInputError("Market document is missing test-only markers.")
        text = str(document.get("text", ""))
        document_terms = set(re.findall(r"[\w가-힣]+", (text + " " + str(document.get("category", ""))).casefold()))
        score = len(query_terms & document_terms)
        document["retrieval_score"] = score
        documents.append(document)
    documents.sort(key=lambda item: (-item["retrieval_score"], item.get("evidence_id", "")))
    retrieved_documents = documents[:5]
    evidence_ids.update(item["evidence_id"] for item in retrieved_documents if item.get("evidence_id"))
    if not observations:
        raise AnalysisInputError("No synthetic observations match the requested category/metric filters.")
    payload = {
        "request_id": request_id,
        "synthetic": True,
        "data_nature": "synthetic_test",
        "permission_status": TEST_ONLY_PERMISSION,
        "operational_eligible": False,
        "usage_restriction": manifest.get("usage_restriction"),
        "generation_metadata": {
            "seed": manifest.get("seed"),
            "manifest_id": manifest.get("manifest_id"),
            "observation_count_in_fixture": manifest.get("observation_count"),
            "corpus_hash": _sha256(observations_path),
        },
        "user_query": query,
        "retrieved_methodology_documents": retrieved_documents,
        "structured_observations": observations,
        "limitations": [
            "Synthetic values are generator assumptions, not observed or forecast market data.",
            "RAG documents explain methodology and taxonomy only; numeric claims must come from supplied structured observations.",
        ],
    }
    return payload, evidence_ids


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_analysis(
    *, domain: str, query: str, request_id: str, client: OpenAIResponsesClient | None = None,
    call: LLMFunction | None = None, hm_permission_reference: str | None = None,
    hm_usage_acknowledged: bool = False, hm_dir: Path = DEFAULT_HM_DIR,
    market_dir: Path = DEFAULT_MARKET_DIR, category: str | None = None, metric: str | None = None,
) -> dict[str, Any]:
    if domain == "sales_analysis":
        payload, evidence_ids = load_hm_sales_context(
            permission_reference=hm_permission_reference,
            usage_acknowledged=hm_usage_acknowledged,
            processed_dir=hm_dir,
        )
        payload["request_id"] = request_id
        payload["user_query"] = query
    elif domain == "market_analysis":
        payload, evidence_ids = retrieve_market_context(
            query=query, request_id=request_id, market_dir=market_dir, category=category, metric=metric,
        )
    else:
        raise AnalysisInputError("domain must be sales_analysis or market_analysis; calls remain separated.")
    payload["evidence_registry"] = sorted(evidence_ids)
    return _call_domain(
        domain=domain, payload=payload, allowed_evidence_ids=evidence_ids, client=client, call=call,
    )

