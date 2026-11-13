"""Single request boundary for R2-backed product research.

R2 remains an object store: source files stay as separate objects. This adapter
centralizes source lookup and lets the HTTP layer collect partial results in one
place. H&M price and age adapters still have separate aggregate queries.
"""

from __future__ import annotations

import os
from typing import Any

from ...market.fabric_price_r2_source import FabricPriceSourceError, R2FabricPriceSource
from .hm_customer_age_source import CustomerAgeSourceError, HmCustomerAgeResearchSource
from .hm_price_source import HmHistoricalPriceResearchSource, HmPriceSourceError


class HmCombinedResearchSource:
    """Return fabric, price and age results through one shared integration layer."""

    def analyze(self, product: dict[str, Any]) -> dict[str, Any]:
        """Fetch each configured source through one request-scoped entry point.

        R2 object storage still requires one object read per distinct object;
        each adapter caches immutable object versions for later requests.
        Independent source failures are returned without suppressing good data.
        """
        result: dict[str, Any] = {
            "fabric_prices": (), "fabric_price_error": None,
            "customer_age_profile": None, "customer_age_error": None,
            "historical_price": None, "historical_price_error": None,
        }
        try:
            result["fabric_prices"] = R2FabricPriceSource().analyze(product)
        except FabricPriceSourceError as exc:
            result["fabric_price_error"] = str(exc)
        if os.getenv("VOGUE_HM_RESEARCH_NONCOMMERCIAL", "").strip().casefold() not in {"1", "true", "yes"}:
            return result
        try:
            result["customer_age_profile"] = HmCustomerAgeResearchSource().profile(
                product.get("category", ""), gender=product.get("gender"),
            )
        except CustomerAgeSourceError as exc:
            result["customer_age_error"] = str(exc)
        try:
            result["historical_price"] = HmHistoricalPriceResearchSource().analyze(
                product.get("category", ""), gender=product.get("gender"),
                product_type=product.get("product_type"), season=product.get("season"),
            )
        except HmPriceSourceError as exc:
            result["historical_price_error"] = str(exc)
        return result
