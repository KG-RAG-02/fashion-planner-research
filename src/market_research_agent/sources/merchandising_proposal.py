"""Build the image-inspired product proposal without fabricating evidence."""

from __future__ import annotations

from typing import Any

from ..domain.models import MarketResearchContext


def build_merchandising_proposal(context: MarketResearchContext) -> dict[str, Any]:
    request = context.planning_request
    query = request.get("query") or request.get("concept") or ""
    metrics = list(context.requested_metrics)
    trend = context.trend_result
    inventory = context.inventory_product_result
    return {
        "format": "merchandising_proposal_dashboard",
        "request_id": context.request_id,
        "concept": {"status": "input_required" if not query else "provided", "brief": query or None},
        "trend_analysis": {"status": "available" if trend and trend.get("status") == "ok" else "no_results", "requested_metrics": metrics, "data": trend if trend and trend.get("status") == "ok" else None},
        "historical_sales": {"status": "pending_source_result", "source": "H&M Kaggle historical data", "observed_period": None, "note": "Past purchase observations only; not current or forecast demand."},
        "market_viability": {"status": "pending_market_source", "data": None, "requested_metrics": metrics},
        "competitor_benchmark": {"status": "not_configured", "data": None},
        "inventory_and_production": {"status": "available" if inventory and inventory.get("status") == "ok" else "not_provided", "data": inventory if inventory and inventory.get("status") == "ok" else None},
        "design_brief": {"status": "input_required", "product_attributes": [], "instructions": []},
        "ai_visualization": {"status": "not_configured", "assets": []},
        "limitations": ["Missing sections remain explicitly unpopulated until authorized source data or upstream inputs are connected."],
    }
