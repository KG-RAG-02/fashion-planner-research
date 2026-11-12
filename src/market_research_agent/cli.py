"""Command-line entry point for the Project_07 market research handoff."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .domain.models import MarketResearchContext
from .sources.shared.composite import CompositeMarketResearchSource
from .sources.sales.hm_kaggle.hm_sales_adapter import HmHistoricalSalesSource
from .sources.sales.hm_kaggle.hm_r2_sales_source import R2HmHistoricalSalesSource
from .sources.sales.hm_kaggle.hm_r2_duckdb_source import R2DuckDBHmHistoricalSalesSource
from .sources.shared.noop import NoConfiguredSources
from .workflows.market_research import MarketResearchAgent
from .query_interpretation import interpret_query, scope_conflicts, unresolved_ambiguities
from .orchestration_request import context_from_canonical_request


def _read_inputs(path: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Input JSON root must be an object.")
    request = payload.get("planning_request")
    trend = payload.get("trend_result")
    inventory = payload.get("inventory_product_result")
    if not isinstance(request, dict):
        raise ValueError("planning_request object is required.")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("planning_request.request_id is required.")
    if request.get("schema_version") != "1.0":
        raise ValueError("planning_request.schema_version must be 1.0.")
    for name, item in (("trend_result", trend), ("inventory_product_result", inventory)):
        if item is not None and not isinstance(item, dict):
            raise ValueError(f"{name} must be an object when supplied.")
    return request, trend, inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--inputs-json", help="Compatibility JSON with planning_request and optional upstream results")
    input_group.add_argument("--canonical-request-json", help="Canonical structured orchestration request proposal; no Trend/Inventory results accepted")
    parser.add_argument("--metrics", default="", help="Optional comma-separated research metrics")
    parser.add_argument("--geography", default="", help="Optional requested market geography; omitted means unspecified")
    parser.add_argument("--channel", default="", help="Optional requested market channel; omitted means unspecified")
    parser.add_argument("--hm-processed-dir", default=os.getenv("VOGUE_HM_PROCESSED_DIR", ""), help="Explicit local/offline H&M processed-data directory.")
    parser.add_argument("--hm-r2-db-key", default=os.getenv("PROJECT07_HM_R2_DB_KEY", ""), help="R2 DuckDB object key; takes priority over the legacy processed CSV bundle.")
    parser.add_argument("--hm-source", choices=("auto", "r2-duckdb", "r2-bundle", "local"), default="auto", help="Select H&M source; auto prefers the shared R2 DuckDB, then the legacy R2 CSV bundle, then an explicitly configured local directory.")
    parser.add_argument("--hm-r2-prefix", default=os.getenv("PROJECT07_HM_R2_PREFIX", ""), help="Explicit R2 prefix for the H&M processed bundle.")
    parser.add_argument("--hm-r2-manifest-key", default=os.getenv("PROJECT07_HM_R2_MANIFEST_KEY", ""), help="Explicit R2 H&M bundle manifest object key.")
    parser.add_argument("--hm-category-map", action="append", default=[], help="Explicit source-to-request category mapping, e.g. 'Garment Upper body=Outerwear'")
    parser.add_argument("--hm-mapping-version", default=os.getenv("VOGUE_HM_MAPPING_VERSION", ""))
    parser.add_argument("--hm-usage-authorized", action="store_true", help="Deprecated compatibility flag; H&M use is enabled by the Project_07 owner.")
    parser.add_argument("--proposal-v1-1", action="store_true", help="Emit the explicitly unratified sales-data extension")
    parser.add_argument("--proposal-v1-2", action="store_true", help="Emit the explicitly unratified separated sales, market, and unit-economics proposal")
    parser.add_argument("--proposal-v1-3", action="store_true", help="Emit the explicitly unratified calculations, comparability, and retrieval proposal")
    args = parser.parse_args(argv)

    try:
        if args.canonical_request_json:
            if args.metrics or args.geography.strip() or args.channel.strip():
                raise ValueError("Canonical request scope cannot be overridden by --metrics, --geography, or --channel.")
            canonical_request = json.loads(Path(args.canonical_request_json).read_text(encoding="utf-8"))
            context = context_from_canonical_request(canonical_request)
        else:
            request, trend, inventory = _read_inputs(args.inputs_json)
            context_data = request.get("context") if isinstance(request.get("context"), dict) else {}
            query = request.get("query") or request.get("original_query") or context_data.get("query")
            if query is not None and not isinstance(query, str):
                raise ValueError("planning_request.query must be a string when supplied.")
            interpreted = interpret_query(query if isinstance(query, str) else None)
            category = context_data.get("category")
            if isinstance(category, dict):
                category = category.get("value")
            category = category if isinstance(category, str) else interpreted["category"]
            explicit_metrics = tuple(x.strip() for x in args.metrics.split(",") if x.strip())
            explicit_scope = {}
            if isinstance(category, str) and isinstance(context_data.get("category"), (str, dict)):
                explicit_scope["category"] = category
            if explicit_metrics:
                explicit_scope["metrics"] = explicit_metrics
            if args.geography.strip():
                explicit_scope["geography"] = args.geography.strip()
            if args.channel.strip():
                explicit_scope["channel"] = args.channel.strip()
            explicit_period = context_data.get("period") if isinstance(context_data.get("period"), str) else request.get("period")
            if isinstance(explicit_period, str):
                explicit_scope["period"] = explicit_period
            gender = context_data.get("gender")
            if isinstance(gender, str):
                explicit_scope["gender"] = gender
            context = MarketResearchContext(
                request_id=request["request_id"], planning_request=request,
                trend_result=trend, inventory_product_result=inventory,
                requested_category=category,
                requested_metrics=explicit_metrics or interpreted["metrics"],
                requested_geography=args.geography.strip() or interpreted["geography"],
                requested_channel=args.channel.strip() or interpreted["channel"],
                requested_query=query.strip() if isinstance(query, str) and query.strip() else None,
                requested_period=(
                    context_data.get("period") if isinstance(context_data.get("period"), str)
                    else request.get("period") if isinstance(request.get("period"), str) else interpreted["period"]
                ),
                inferred_scope={key: value for key, value in interpreted.items() if value not in (None, ())},
                explicit_scope=explicit_scope,
                query_intent=str(interpreted["intent"]),
                query_ambiguities=unresolved_ambiguities(interpreted, explicit_scope) + scope_conflicts(interpreted, explicit_scope),
                parser_version=str(interpreted["parser_version"]),
            )
        sources = []
        if args.hm_category_map:
            if not args.hm_mapping_version:
                raise ValueError("--hm-mapping-version is required with --hm-category-map.")
            category_map = {}
            for pair in args.hm_category_map:
                source, separator, target = pair.partition("=")
                if not separator or not source.strip() or not target.strip():
                    raise ValueError("Each --hm-category-map must use source=target format.")
                category_map[source.strip()] = target.strip()
            # Kept for CLI compatibility. The owner has enabled this historical source.
            hm_usage_authorized = True
            r2_db_key = args.hm_r2_db_key.strip() or os.getenv("R2_DB_KEY", "").strip()
            source_mode = args.hm_source
            if source_mode == "auto":
                source_mode = ("r2-duckdb" if r2_db_key else
                               "r2-bundle" if args.hm_r2_prefix or args.hm_r2_manifest_key else
                               "local" if args.hm_processed_dir else "")
            if source_mode == "r2-duckdb" and r2_db_key:
                sources.append(R2DuckDBHmHistoricalSalesSource(
                    category_map, args.hm_mapping_version,
                    usage_authorized=hm_usage_authorized,
                    object_key=r2_db_key,
                ))
            elif source_mode == "r2-bundle" and (args.hm_r2_prefix or args.hm_r2_manifest_key):
                sources.append(R2HmHistoricalSalesSource(
                    category_map, args.hm_mapping_version,
                    usage_authorized=hm_usage_authorized,
                    allowed_prefix=args.hm_r2_prefix,
                    manifest_key=args.hm_r2_manifest_key,
                ))
            elif source_mode == "local" and args.hm_processed_dir:
                sources.append(HmHistoricalSalesSource(
                    Path(args.hm_processed_dir), category_map, args.hm_mapping_version,
                    usage_authorized=hm_usage_authorized,
                ))
            else:
                raise ValueError("The selected H&M source is not configured; verify its object key or processed directory.")
        source = CompositeMarketResearchSource(sources) if sources else NoConfiguredSources()
        result = MarketResearchAgent(source).run(context)
        if sum((args.proposal_v1_1, args.proposal_v1_2, args.proposal_v1_3)) > 1:
            raise ValueError("Choose only one proposal schema version.")
        output = (
            result.to_proposal_v1_3_dict()
            if args.proposal_v1_3
            else
            result.to_proposal_v1_2_dict()
            if args.proposal_v1_2
            else result.to_proposal_dict()
            if args.proposal_v1_1
            else result.to_dict()
        )
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
