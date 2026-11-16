"""Industry gross-margin and H&M price-range benchmark calculations."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
from typing import Any



APPAREL_INDUSTRY_GROSS_MARGIN_BENCHMARK_RATE = Decimal("0.5688")


class MarginAnalysisInputError(ValueError):
    """Invalid inputs for deterministic apparel benchmark calculations."""


def _decimal(value: int | float) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise MarginAnalysisInputError("Margin analysis input could not be represented as a decimal number.") from None


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_benchmark_margin_estimate(
    *, selling_price: int | float | None, currency: str | None,
    fabric_unit_cost: int | float | None, fabric_unit: str | None,
    fabric_consumption: int | float | None = None,
) -> dict[str, Any]:
    """Use the NYU Stern US apparel gross-margin benchmark.

    This does not assert the product's actual gross margin. A fabric-only input is
    compared against the benchmark's implied total COGS allowance only if garment
    consumption is explicitly supplied.
    """
    rate = APPAREL_INDUSTRY_GROSS_MARGIN_BENCHMARK_RATE
    out: dict[str, Any] = {
        "status": "benchmark_estimate",
        "estimate_type": "industry_average_gross_margin_benchmark",
        "basis": "nyu_stern_us_apparel_gross_margin_january_2026",
        "benchmark_gross_margin_rate": float(rate),
        "estimated_gross_margin_rate": float(rate),
        "benchmark_source": "NYU Stern Damodaran Margins by Sector (US), January 2026",
        "benchmark_sample_size": 35,
        "benchmark_region": "US",
        "formula": {
            "benchmark_implied_cost_allowance": "selling_price * (1 - apparel_industry_gross_margin_rate)",
            "fabric_cost_per_garment": "fabric_unit_cost * fabric_consumption",
            "remaining_cost_allowance": "benchmark_implied_cost_allowance - fabric_cost_per_garment",
        },
        "selling_price": selling_price,
        "currency": currency.upper() if isinstance(currency, str) else None,
        "limitations": [
            "The 56.88% rate is an average for 35 US public apparel companies; it is not a Korea-specific or product-specific forecast.",
            "A public fabric listing reference is not a verified purchasing cost.",
            "This benchmark estimate represents US apparel sector economics; it is not a product-specific margin observation.",
        ],
    }
    if fabric_unit_cost is not None:
        out["fabric_unit_cost"] = fabric_unit_cost
        out["fabric_unit"] = fabric_unit
    if fabric_consumption is not None:
        out["fabric_consumption_per_garment"] = fabric_consumption
    if (isinstance(selling_price, (int, float)) and not isinstance(selling_price, bool)
            and math.isfinite(selling_price) and selling_price > 0):
        price = _decimal(selling_price)
        out["implied_total_cost_allowance"] = _money(price * (Decimal("1") - rate))
    if (fabric_unit_cost is not None and fabric_consumption is not None
            and isinstance(fabric_unit_cost, (int, float)) and not isinstance(fabric_unit_cost, bool)
            and isinstance(fabric_consumption, (int, float)) and not isinstance(fabric_consumption, bool)
            and math.isfinite(fabric_unit_cost) and math.isfinite(fabric_consumption)
            and fabric_unit_cost >= 0 and fabric_consumption > 0):
        fabric_total = _decimal(fabric_unit_cost) * _decimal(fabric_consumption)
        out["fabric_cost_per_garment"] = _money(fabric_total)
        out["fabric_consumption_per_garment"] = fabric_consumption
        allowance = out["implied_total_cost_allowance"]
        if allowance is not None:
            remaining = Decimal(str(allowance)) - fabric_total
            out["remaining_cost_allowance_after_fabric"] = _money(remaining)
            out["limitations"].append(
                "Remaining allowance is the benchmark cost budget for all non-fabric costs; it is not realized margin."
            )
    elif fabric_unit_cost is not None:
        out["missing_inputs"] = ["fabric_consumption_per_garment"]
    return out


def calculate_price_range_margin_analysis(
    *, scaled_price_statistics: dict[str, Any], usd_krw_rate: int | float,
    fabric_unit_cost: int | float, fabric_unit: str,
    fx_rate_date: str | None = None, fx_provider: str | None = None,
    fx_fallback_used: bool = False, fx_rate_stale: bool = False,
    fabric_reference_record_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Compare the H&M P25–P75 sale-price range against apparel gross margin.

    Since fabric prices are per meter, the residual is calculated after one meter
    of fabric reference. This is a per-meter comparison, not a garment BOM.
    """
    if not isinstance(scaled_price_statistics, dict):
        raise MarginAnalysisInputError("Scaled price statistics are required for range analysis.")
    rate = APPAREL_INDUSTRY_GROSS_MARGIN_BENCHMARK_RATE
    fx = _decimal(usd_krw_rate)
    fabric_cost = _decimal(fabric_unit_cost)
    if fx <= 0 or fabric_cost < 0:
        raise MarginAnalysisInputError("FX and fabric reference prices must be valid non-negative amounts.")

    def _price(key: str) -> Decimal:
        value = scaled_price_statistics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise MarginAnalysisInputError(f"Scaled price statistic {key!r} is unavailable or invalid.")
        return _decimal(value)

    def _point(price_usd: Decimal) -> dict[str, float]:
        price_krw = price_usd * fx
        total_cost_allowance = price_krw * (Decimal("1") - rate)
        gross_profit_allowance = price_krw * rate
        residual = total_cost_allowance - fabric_cost
        return {
            "price_usd": _money(price_usd),
            "price_krw": _money(price_krw),
            "gross_profit_allowance_krw": _money(gross_profit_allowance),
            "total_cost_allowance_krw": _money(total_cost_allowance),
            "fabric_reference_cost_krw_per_meter": _money(fabric_cost),
            "remaining_non_fabric_cost_allowance_krw_after_one_meter": _money(residual),
        }

    ranges = {
        "quartile_p25_p75": (_price("p25"), _price("p75")),
    }
    comparisons = {}
    for name, (lower, upper) in ranges.items():
        comparisons[name] = {
            "lower_bound": _point(lower),
            "upper_bound": _point(upper),
        }
    return {
        "status": "benchmark_estimate",
        "benchmark_rate_type": "gross_margin",
        "benchmark_rate_percent": float(rate * 100),
        "benchmark_source": "NYU Stern Damodaran Margins by Sector (US), January 2026",
        "benchmark_sample_size": 35,
        "benchmark_region": "US",
        "benchmark_cost_ratio_percent": float((Decimal("1") - rate) * 100),
        "fx_rate_krw_per_usd": float(fx),
        "fx_rate_date": fx_rate_date,
        "fx_provider": fx_provider,
        "fx_fallback_used": fx_fallback_used,
        "fx_rate_stale": fx_rate_stale,
        "currency": "KRW",
        "fabric_cost_basis": "one_meter_reference; not garment consumption",
        "fabric_unit": fabric_unit,
        "fabric_reference_sample_count": len(fabric_reference_record_ids or []),
        "fabric_reference_record_ids": list(fabric_reference_record_ids or []),
        "ranges": comparisons,
        "formula": {
            "gross_profit_allowance": "sale_price_krw * industry_gross_margin_rate",
            "total_cost_allowance": "sale_price_krw * (1 - industry_gross_margin_rate)",
            "remaining_non_fabric_cost_allowance": "total_cost_allowance - fabric_reference_cost_per_meter",
            "price_conversion": "sale_price_usd * usd_krw_reference_rate",
        },
        "limitations": [
            "The benchmark is the average for 35 US public apparel companies, not a product-specific or Korea-specific margin forecast.",
            "The remaining allowance subtracts one meter of public listing reference price; it is not a per-garment cost without fabric consumption data.",
            "A negative remaining allowance means the benchmark cost budget at that price bound is below the one-meter fabric reference.",
        ],
    }
