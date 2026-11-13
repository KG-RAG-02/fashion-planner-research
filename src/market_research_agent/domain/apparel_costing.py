"""Evidence-traceable apparel BOM/SAM/FOB and retail gross-margin costing."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re
from typing import Any


COSTING_SCHEMA_VERSION = "1.0"
COSTING_FORMULA_VERSION = "apparel-bom-sam-landed-gm/1.0"
APPAREL_INDUSTRY_GROSS_MARGIN_BENCHMARK_RATE = Decimal("0.5688")
FACTORY_COST_FIELDS = ("processing", "finishing", "packaging", "inspection")
LOGISTICS_COST_FIELDS = ("freight", "insurance", "duty", "inland_logistics")


class CostingInputError(ValueError):
    """Invalid supplied costing data; callers must reject before calculation."""


def _keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CostingInputError(f"{field} fields do not match the costing schema.")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float = 0,
    maximum: float | None = None,
    exclusive_min: bool = False,
    exclusive_max: bool = False,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CostingInputError(f"{field} must be a finite number.")
    if (value <= minimum if exclusive_min else value < minimum) or (
        maximum is not None and (value >= maximum if exclusive_max else value > maximum)
    ):
        raise CostingInputError(f"{field} is outside its allowed range.")


def _source_ref(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise CostingInputError(f"{field}.source_ref is required and must be at most 240 characters.")


def validate_apparel_costing(value: Any) -> dict[str, Any]:
    """Validate explicit, same-currency cost inputs. Zero must be stated explicitly."""
    costing = _keys(value, {
        "schema_version", "currency", "price_basis", "materials", "labor",
        "factory_costs", "factory_markup_rate", "logistics_costs",
        "discount_rate", "return_rate", "target_gross_margin_rate", "rate_source_refs",
    }, "costing")
    if costing["schema_version"] != COSTING_SCHEMA_VERSION:
        raise CostingInputError("costing.schema_version must be 1.0.")
    currency = costing["currency"]
    if not isinstance(currency, str) or re.fullmatch(r"[A-Za-z]{3}", currency) is None:
        raise CostingInputError("costing.currency must be a three-letter currency code.")
    if costing["price_basis"] != "ex_tax":
        raise CostingInputError("costing.price_basis must be ex_tax; tax conversion is not inferred.")

    materials = costing["materials"]
    if not isinstance(materials, list) or not materials:
        raise CostingInputError("costing.materials must contain fabric and trim BOM lines.")
    seen_kinds: set[str] = set()
    for index, raw in enumerate(materials):
        field = f"costing.materials[{index}]"
        item = _keys(raw, {"kind", "name", "unit_cost", "consumption", "unit", "waste_rate", "source_ref"}, field)
        if item["kind"] not in {"fabric", "trims"}:
            raise CostingInputError(f"{field}.kind must be fabric or trims.")
        seen_kinds.add(item["kind"])
        for name in ("name", "unit"):
            if not isinstance(item[name], str) or not item[name].strip() or len(item[name]) > 120:
                raise CostingInputError(f"{field}.{name} is required.")
        _number(item["unit_cost"], f"{field}.unit_cost")
        _number(item["consumption"], f"{field}.consumption", exclusive_min=True)
        _number(item["waste_rate"], f"{field}.waste_rate")
        _source_ref(item["source_ref"], field)
    if seen_kinds != {"fabric", "trims"}:
        raise CostingInputError("costing.materials must explicitly include fabric and trims, including zero-cost lines when applicable.")

    labor = _keys(costing["labor"], {
        "sam_minutes", "working_minute_cost", "efficiency_rate", "source_ref",
    }, "costing.labor")
    _number(labor["sam_minutes"], "costing.labor.sam_minutes", exclusive_min=True)
    _number(labor["working_minute_cost"], "costing.labor.working_minute_cost")
    _number(labor["efficiency_rate"], "costing.labor.efficiency_rate", minimum=0, maximum=1, exclusive_min=True, exclusive_max=True)
    _source_ref(labor["source_ref"], "costing.labor")

    for block, required_fields in (("factory_costs", FACTORY_COST_FIELDS), ("logistics_costs", LOGISTICS_COST_FIELDS)):
        costs = _keys(costing[block], set(required_fields), f"costing.{block}")
        for name in required_fields:
            item = _keys(costs[name], {"amount", "source_ref"}, f"costing.{block}.{name}")
            _number(item["amount"], f"costing.{block}.{name}.amount")
            _source_ref(item["source_ref"], f"costing.{block}.{name}")

    _number(costing["factory_markup_rate"], "costing.factory_markup_rate")
    _number(costing["discount_rate"], "costing.discount_rate", maximum=1, exclusive_max=True)
    _number(costing["return_rate"], "costing.return_rate", maximum=1, exclusive_max=True)
    target_margin = costing["target_gross_margin_rate"]
    if target_margin is not None:
        _number(target_margin, "costing.target_gross_margin_rate", maximum=1, exclusive_max=True)
    rate_refs = _keys(costing["rate_source_refs"], {
        "factory_markup_rate", "discount_rate", "return_rate", "target_gross_margin_rate",
    }, "costing.rate_source_refs")
    for field in ("factory_markup_rate", "discount_rate", "return_rate"):
        _source_ref(rate_refs[field], f"costing.rate_source_refs.{field}")
    target_ref = rate_refs["target_gross_margin_rate"]
    if target_margin is None and target_ref is not None:
        raise CostingInputError("target margin reference must be null when target_gross_margin_rate is null.")
    if target_margin is not None:
        _source_ref(target_ref, "costing.rate_source_refs.target_gross_margin_rate")
    return costing


def _decimal(value: int | float) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise CostingInputError("Cost input could not be represented as a decimal number.") from None


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
            "required_selling_price_from_complete_cost": "complete_cost / (1 - benchmark_gross_margin_rate)",
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
        raise CostingInputError("Scaled price statistics are required for range analysis.")
    rate = APPAREL_INDUSTRY_GROSS_MARGIN_BENCHMARK_RATE
    fx = _decimal(usd_krw_rate)
    fabric_cost = _decimal(fabric_unit_cost)
    if fx <= 0 or fabric_cost < 0:
        raise CostingInputError("FX and fabric reference prices must be valid non-negative amounts.")

    def _price(key: str) -> Decimal:
        value = scaled_price_statistics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise CostingInputError(f"Scaled price statistic {key!r} is unavailable or invalid.")
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


def calculate_apparel_margin(
    *,
    proposed_price: int | float | None,
    price_currency: str | None,
    costing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a margin result only when the complete explicit costing sheet exists."""
    if costing is None:
        return {
            "status": "no_results",
            "basis_price": proposed_price,
            "currency": price_currency,
            "calculations": [],
            "limitations": ["A complete, source-referenced apparel costing sheet is required."],
            "errors": [],
            "missing_data": [{
                "field": "margin_estimate", "reason": "not_available_from_source",
                "message": "Supply explicit BOM, SAM labor, factory, logistics, and selling adjustment inputs.",
            }],
        }
    validate_apparel_costing(costing)
    currency = costing["currency"].upper()
    if proposed_price is None or price_currency is None:
        return {
            "status": "no_results", "basis_price": proposed_price, "currency": currency,
            "calculations": [], "limitations": ["A proposed ex-tax selling price and currency are required."],
            "errors": [], "missing_data": [{
                "field": "margin_estimate", "reason": "not_available_from_source",
                "message": "PlanningDraft product.proposed_price and product.currency are required.",
            }],
        }
    if price_currency.upper() != currency:
        return {
            "status": "no_results", "basis_price": proposed_price, "currency": currency,
            "calculations": [], "limitations": ["Price and costing currencies differ; no exchange rate was supplied or inferred."],
            "errors": [], "missing_data": [{
                "field": "margin_estimate", "reason": "invalid_data",
                "message": "Convert all inputs using an approved exchange rate and common as-of date before costing.",
            }],
        }
    if isinstance(proposed_price, bool) or not isinstance(proposed_price, (int, float)) or not math.isfinite(proposed_price) or proposed_price <= 0:
        raise CostingInputError("proposed_price must be a positive finite number.")

    materials: list[dict[str, Any]] = []
    material_total = Decimal("0")
    for item in costing["materials"]:
        cost = _decimal(item["unit_cost"]) * _decimal(item["consumption"]) * (Decimal("1") + _decimal(item["waste_rate"]))
        amount = _money(cost)
        material_total += Decimal(str(amount))
        materials.append({
            "kind": item["kind"], "name": item["name"], "unit_cost": _money(_decimal(item["unit_cost"])),
            "consumption": item["consumption"], "unit": item["unit"], "waste_rate": item["waste_rate"],
            "cost": amount, "source_ref": item["source_ref"],
        })
    labor_input = costing["labor"]
    labor_cost = (
        _decimal(labor_input["sam_minutes"])
        * _decimal(labor_input["working_minute_cost"])
        / _decimal(labor_input["efficiency_rate"])
    )
    factory_rows: list[dict[str, Any]] = []
    factory_extras = Decimal("0")
    for component in FACTORY_COST_FIELDS:
        line = costing["factory_costs"][component]
        amount = _money(_decimal(line["amount"]))
        factory_extras += Decimal(str(amount))
        factory_rows.append({"component": component, "amount": amount, "source_ref": line["source_ref"]})
    production_cost = material_total + Decimal(str(_money(labor_cost))) + factory_extras
    factory_markup = production_cost * _decimal(costing["factory_markup_rate"])
    fob_quote = production_cost + factory_markup
    logistics_rows: list[dict[str, Any]] = []
    landed_addons = Decimal("0")
    for component in LOGISTICS_COST_FIELDS:
        line = costing["logistics_costs"][component]
        amount = _money(_decimal(line["amount"]))
        landed_addons += Decimal(str(amount))
        logistics_rows.append({"component": component, "amount": amount, "source_ref": line["source_ref"]})
    landed_cost = fob_quote + landed_addons
    net_sales = (
        _decimal(proposed_price)
        * (Decimal("1") - _decimal(costing["discount_rate"]))
        * (Decimal("1") - _decimal(costing["return_rate"]))
    )
    if net_sales <= 0:
        raise CostingInputError("Discount and return adjustments leave no positive net sales value.")
    gross_profit = net_sales - landed_cost
    gross_margin = gross_profit / net_sales
    target_rate = costing["target_gross_margin_rate"]
    target_price = None
    if target_rate is not None:
        target_price = landed_cost / (
            (Decimal("1") - _decimal(costing["discount_rate"]))
            * (Decimal("1") - _decimal(costing["return_rate"]))
            * (Decimal("1") - _decimal(target_rate))
        )
    return {
        "status": "ok",
        "formula_id": COSTING_FORMULA_VERSION,
        "formula": {
            "material_cost": "sum(unit_cost * consumption * (1 + waste_rate))",
            "labor_cost": "SAM_minutes * working_minute_cost / efficiency_rate",
            "production_cost": "material_cost + labor_cost + processing + finishing + packaging + inspection",
            "FOB": "production_cost * (1 + factory_markup_rate)",
            "landed_cost": "FOB + freight + insurance + duty + inland_logistics",
            "net_sales_per_unit": "ex_tax_list_price * (1 - discount_rate) * (1 - return_rate)",
            "gross_margin_rate": "(net_sales_per_unit - landed_cost) / net_sales_per_unit",
            "required_list_price_for_target_margin": "landed_cost / ((1-discount_rate) * (1-return_rate) * (1-target_gross_margin_rate))",
        },
        "estimate_type": "caller_supplied_cost_sheet",
        "currency": currency,
        "basis_price": _money(_decimal(proposed_price)),
        "price_basis": "ex_tax_list_price_before_discount_and_returns",
        "materials": materials,
        "labor": {
            "sam_minutes": labor_input["sam_minutes"],
            "working_minute_cost": _money(_decimal(labor_input["working_minute_cost"])),
            "efficiency_rate": labor_input["efficiency_rate"],
            "labor_cost": _money(labor_cost), "source_ref": labor_input["source_ref"],
        },
        "factory_costs": factory_rows,
        "factory_markup_rate": costing["factory_markup_rate"],
        "logistics_costs": logistics_rows,
        "discount_rate": costing["discount_rate"],
        "return_rate": costing["return_rate"],
        "rate_source_refs": costing["rate_source_refs"],
        "calculation": {
            "material_cost": _money(material_total),
            "production_cost_before_markup": _money(production_cost),
            "factory_markup": _money(factory_markup),
            "fob_quote": _money(fob_quote),
            "landed_cost": _money(landed_cost),
            "net_sales_per_unit": _money(net_sales),
            "gross_profit_per_unit": _money(gross_profit),
            "gross_margin_rate": round(float(gross_margin), 6),
            "target_gross_margin_rate": target_rate,
            "required_list_price_for_target_margin": _money(target_price) if target_price is not None else None,
        },
        "source_refs": sorted({
            *(item["source_ref"] for item in costing["materials"]),
            labor_input["source_ref"],
            *(line["source_ref"] for line in costing["factory_costs"].values()),
            *(line["source_ref"] for line in costing["logistics_costs"].values()),
            *(value for value in costing["rate_source_refs"].values() if value is not None),
        }),
        "assumptions": [
            "All cost lines are per garment and use the stated currency; no currency conversion is performed.",
            "Working-minute cost includes direct labor, indirect labor, and factory overhead; factory cost lines must contain only per-piece costs not already included in that minute rate.",
            "Factory markup is applied to material, labor, and other factory production costs to form the FOB quote.",
            "Landed cost adds freight, insurance, duty, and inland logistics to the FOB quote.",
            "Net sales per unit applies discount and unrecovered return rates multiplicatively to the ex-tax list price; return recovery and reverse-logistics costs are not modeled.",
            "Gross margin is (net sales per unit - landed cost) / net sales per unit; it excludes selling, marketing, payment, and corporate operating expenses.",
        ],
        "limitations": ["This is a costing estimate from supplied BOM/factory/logistics inputs; it is not a supplier quote or net profit forecast. Taxes, supplier commission, selling/marketing/payment costs, and return recovery costs are excluded."],
        "source_reference_status": "Caller-provided references are recorded but are not independently verified by this service.",
        "errors": [],
        "missing_data": [],
    }
