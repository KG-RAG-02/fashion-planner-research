"""Command line entry point for a traceable merchandising research result."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .domain.models import MarketResearchContext
from .sources.composite import CompositeMarketResearchSource
from .sources.hm_historical.hm_sales_adapter import HmHistoricalSalesSource
from .sources.market_data import UnconfiguredMarketDataSource
from .sources.merchandising_proposal import build_merchandising_proposal
from .workflows.market_research import MarketResearchAgent


def _category_map(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        if "=" not in pair:
            raise argparse.ArgumentTypeError("Category mappings must use Source Category=Requested Category.")
        source, target = pair.split("=", 1)
        if not source.strip() or not target.strip():
            raise argparse.ArgumentTypeError("Category mapping entries must not be blank.")
        result[source.strip()] = target.strip()
    if not result:
        raise argparse.ArgumentTypeError("At least one explicit category mapping is required.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a source-traceable merchandising research result.")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--metrics", default="", help="Comma-separated requested market metrics")
    parser.add_argument("--planning-request", default="", help="Concept / planning brief")
    parser.add_argument("--hm-processed-dir", type=Path, default=Path(os.getenv("VOGUE_HM_PROCESSED_DIR", "data/processed/hm-personalized-fashion-recommendations")))
    parser.add_argument("--hm-category-map", type=_category_map, default=None, help="Explicit mappings such as 'Garment Upper body=Outerwear'")
    parser.add_argument("--hm-mapping-version", default=os.getenv("VOGUE_HM_MAPPING_VERSION", ""))
    parser.add_argument("--hm-usage-authorized", action="store_true", help="Assert local approval exists for the intended Kaggle data use")
    parser.add_argument("--proposal-v1-1", action="store_true", help="Emit explicit 1.1 proposal serialization; default is compatible 1.0")
    args = parser.parse_args(argv)

    sources = [UnconfiguredMarketDataSource()]
    mapping = args.hm_category_map or {}
    if args.hm_mapping_version:
        sources.insert(0, HmHistoricalSalesSource(args.hm_processed_dir, mapping, args.hm_mapping_version, usage_authorized=args.hm_usage_authorized))
    context = MarketResearchContext(
        request_id=args.request_id,
        planning_request={"query": args.planning_request, "category": args.category, "requested_metrics": [m.strip() for m in args.metrics.split(",") if m.strip()]},
        requested_category=args.category,
        requested_metrics=tuple(m.strip() for m in args.metrics.split(",") if m.strip()),
    )
    result = MarketResearchAgent(CompositeMarketResearchSource(sources)).run(context)
    from dataclasses import replace
    proposal = build_merchandising_proposal(context)
    if result.sales_data is None:
        proposal["historical_sales"]["status"] = "no_results"
        proposal["historical_sales"]["limitations"] = [
            "H&M processed dataset or explicit category mapping is not configured."
        ]
    if result.sales_data is not None:
        proposal["historical_sales"] = {
            "status": result.sales_data.status,
            "source": "H&M Kaggle historical data",
            "observed_period": next((
                {"start": item.period_start, "end": item.period_end}
                for item in result.sales_data.observations
            ), None),
            "observations": [item.to_dict() for item in result.sales_data.observations],
            "note": "Past purchase observations only; not current or forecast demand.",
            "limitations": list(result.sales_data.limitations),
        }
    proposal["market_viability"]["status"] = "no_results"
    proposal["market_viability"]["limitations"] = [
        item for item in result.limitations if "market" in item.casefold() or "provider" in item.casefold()
    ] or ["No market provider is configured."]
    result = replace(result, proposal=proposal)
    payload = result.to_proposal_dict() if args.proposal_v1_1 else result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
