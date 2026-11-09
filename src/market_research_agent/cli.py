"""Command-line entry point for the Project_07 market research handoff."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .domain.models import MarketResearchContext
from .sources.composite import CompositeMarketResearchSource
from .sources.hm_historical.hm_sales_adapter import HmHistoricalSalesSource
from .sources.local_market_csv import LocalMarketCsvSource
from .sources.noop import NoConfiguredSources
from .workflows.market_research import MarketResearchAgent


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
        if isinstance(item, dict) and item.get("request_id") != request_id:
            raise ValueError(f"{name} request_id does not match planning_request.")
        if isinstance(item, dict) and item.get("schema_version") != "1.0":
            raise ValueError(f"{name}.schema_version must be 1.0.")
    return request, trend, inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-json", required=True, help="JSON object with planning_request, trend_result, inventory_product_result")
    parser.add_argument("--metrics", default="", help="Optional comma-separated research metrics")
    parser.add_argument("--geography", default="KR", help="Requested market geography (default: KR)")
    parser.add_argument("--channel", default="online", help="Requested market channel (default: online)")
    parser.add_argument("--hm-processed-dir", default=os.getenv("VOGUE_HM_PROCESSED_DIR", "data/processed/hm-personalized-fashion-recommendations"))
    parser.add_argument("--hm-category-map", action="append", default=[], help="Explicit source-to-request category mapping, e.g. 'Garment Upper body=Outerwear'")
    parser.add_argument("--hm-mapping-version", default=os.getenv("VOGUE_HM_MAPPING_VERSION", ""))
    parser.add_argument("--hm-usage-authorized", action="store_true", help="Declare externally confirmed authorization for this source")
    parser.add_argument("--market-observations-csv", default="", help="Explicit local CSV path for authorized market observations")
    parser.add_argument("--market-manifest", default="", help="Explicit source/permission manifest path for the market CSV")
    parser.add_argument("--proposal-v1-1", action="store_true", help="Emit the explicitly unratified sales-data extension")
    parser.add_argument("--proposal-v1-2", action="store_true", help="Emit the explicitly unratified separated sales, market, and unit-economics proposal")
    args = parser.parse_args(argv)

    try:
        request, trend, inventory = _read_inputs(args.inputs_json)
        context_data = request.get("context") if isinstance(request.get("context"), dict) else {}
        category = context_data.get("category")
        if isinstance(category, dict):
            category = category.get("value")
        category = category if isinstance(category, str) else None
        context = MarketResearchContext(
            request_id=request["request_id"], planning_request=request,
            trend_result=trend, inventory_product_result=inventory,
            requested_category=category,
            requested_metrics=tuple(x.strip() for x in args.metrics.split(",") if x.strip()),
            requested_geography=args.geography.strip() or None,
            requested_channel=args.channel.strip() or None,
        )
        sources = []
        if bool(args.market_observations_csv) != bool(args.market_manifest):
            raise ValueError("--market-observations-csv and --market-manifest must be supplied together.")
        if args.market_observations_csv:
            sources.append(LocalMarketCsvSource(
                Path(args.market_observations_csv), Path(args.market_manifest)
            ))
        if args.hm_category_map:
            if not args.hm_mapping_version:
                raise ValueError("--hm-mapping-version is required with --hm-category-map.")
            category_map = {}
            for pair in args.hm_category_map:
                source, separator, target = pair.partition("=")
                if not separator or not source.strip() or not target.strip():
                    raise ValueError("Each --hm-category-map must use source=target format.")
                category_map[source.strip()] = target.strip()
            sources.append(HmHistoricalSalesSource(
                Path(args.hm_processed_dir), category_map, args.hm_mapping_version,
                usage_authorized=args.hm_usage_authorized,
            ))
        source = CompositeMarketResearchSource(sources) if sources else NoConfiguredSources()
        result = MarketResearchAgent(source).run(context)
        if args.proposal_v1_1 and args.proposal_v1_2:
            raise ValueError("Choose only one proposal schema version.")
        output = (
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
