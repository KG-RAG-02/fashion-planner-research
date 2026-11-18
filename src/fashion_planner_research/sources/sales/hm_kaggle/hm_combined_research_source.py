"""Coordinate independent product research sources and preserve partial results."""

from __future__ import annotations

import os
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ...market.fabric_price_r2_source import FabricPriceSourceError, R2FabricPriceSource
from ....runtime_settings import hm_research_noncommercial_enabled
from .hm_customer_age_source import CustomerAgeSourceError, HmCustomerAgeResearchSource
from .hm_price_source import HmHistoricalPriceResearchSource, HmPriceSourceError
from .hm_research_aggregate import HmResearchAggregateSource

_LOGGER = logging.getLogger(__name__)
_RESEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="research-source")


class HmCombinedResearchSource:
    """Return fabric, price and age results through one shared integration layer."""

    def analyze(self, product: dict[str, Any]) -> dict[str, Any]:
        """Use one combined snapshot when configured; otherwise query sources in parallel."""
        started_at = time.perf_counter()
        result: dict[str, Any] = {
            "fabric_prices": (), "fabric_price_error": None,
            "fabric_price_source_status": "not_configured",
            "customer_age_profile": None, "customer_age_error": None,
            "customer_age_source_status": "disabled",
            "historical_price": None, "historical_price_error": None,
            "historical_price_source_status": "disabled",
        }
        fabric_settings = (
            "R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT",
        )
        if any(os.getenv(name, "").strip() for name in fabric_settings):
            result["fabric_price_source_status"] = "configured"
        research_enabled = hm_research_noncommercial_enabled()
        if research_enabled:
            result["customer_age_source_status"] = "configured"
            result["historical_price_source_status"] = "configured"
        aggregate_key = os.getenv("VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY", "").strip()
        _LOGGER.info(
            "H&M research mode: %s",
            "combined_aggregate_snapshot" if research_enabled and aggregate_key else
            "independent_raw_objects" if research_enabled else "disabled",
        )

        def read_fabric():
            return R2FabricPriceSource().analyze(product)

        def read_age():
            return HmCustomerAgeResearchSource().profile(
                product.get("category", ""), gender=product.get("gender"),
                product_type=product.get("product_type"),
            )

        def read_price():
            return HmHistoricalPriceResearchSource().analyze(
                product.get("category", ""), gender=product.get("gender"),
                product_type=product.get("product_type"), season=product.get("season"),
            )

        def read_aggregate():
            return HmResearchAggregateSource().analyze(product)

        if research_enabled and aggregate_key:
            try:
                aggregate = read_aggregate()
                result.update(aggregate)
            except Exception as exc:
                _LOGGER.warning("Combined H&M research failed: %s", type(exc).__name__)
                message = f"H&M aggregate snapshot failed ({type(exc).__name__})."
                result["customer_age_error"] = message
                result["historical_price_error"] = message
                result["fabric_price_error"] = message
                result["customer_age_source_status"] = "error"
                result["historical_price_source_status"] = "error"
                result["fabric_price_source_status"] = "error"
            _LOGGER.info("Combined research completed in %.3fs", time.perf_counter() - started_at)
            return result

        tasks = {"fabric": read_fabric}
        if research_enabled:
            tasks["age"] = read_age
            tasks["price"] = read_price
        errors = {
            "fabric": FabricPriceSourceError,
            "age": CustomerAgeSourceError,
            "price": HmPriceSourceError,
        }
        futures = {}
        started = {}
        for name, fn in tasks.items():
            future = _RESEARCH_EXECUTOR.submit(fn)
            futures[future] = name
            started[future] = time.perf_counter()
        for future in as_completed(futures):
            name = futures[future]
            elapsed = time.perf_counter() - started[future]
            try:
                value = future.result()
            except errors[name] as exc:
                _LOGGER.warning("%s research failed after %.3fs: %s", name, elapsed, type(exc).__name__)
                result[{"fabric": "fabric_price_error", "age": "customer_age_error",
                        "price": "historical_price_error"}[name]] = str(exc)
                if name == "fabric":
                    result["fabric_price_source_status"] = "error"
                elif name == "age":
                    result["customer_age_source_status"] = "error"
                else:
                    result["historical_price_source_status"] = "error"
                continue
            except Exception as exc:
                _LOGGER.warning("%s research failed after %.3fs: %s", name, elapsed, type(exc).__name__)
                result[{"fabric": "fabric_price_error", "age": "customer_age_error",
                        "price": "historical_price_error"}[name]] = (
                    f"Unexpected {name} research failure ({type(exc).__name__})."
                )
                if name == "fabric":
                    result["fabric_price_source_status"] = "error"
                elif name == "age":
                    result["customer_age_source_status"] = "error"
                else:
                    result["historical_price_source_status"] = "error"
                continue
            _LOGGER.info("%s research completed in %.3fs", name, elapsed)
            if name == "fabric":
                result["fabric_prices"] = value
                result["fabric_price_source_status"] = (
                    "available" if value else
                    "no_match" if result["fabric_price_source_status"] == "configured" else
                    "not_configured"
                )
            elif name == "age":
                result["customer_age_profile"] = value
                result["customer_age_source_status"] = "available" if value is not None else "no_match"
            else:
                result["historical_price"] = value
                result["historical_price_source_status"] = "available" if value is not None else "no_match"
        _LOGGER.info("Independent research sources completed in %.3fs", time.perf_counter() - started_at)
        return result
