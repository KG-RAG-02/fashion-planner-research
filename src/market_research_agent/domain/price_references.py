"""Read-only analysis of explicitly supplied market-price snapshots.

This module does not fetch pages. Snapshot files must be obtained through a
permitted route and identify their source, timestamp, and usage permission.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
from statistics import median
from typing import Any


class SnapshotError(ValueError):
    """A configured snapshot is malformed or cannot be safely used."""


def _read_rows(env_name: str) -> list[dict[str, str]]:
    path = os.environ.get(env_name, "").strip()
    if not path:
        return []
    file = Path(path)
    try:
        with file.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise SnapshotError(f"{env_name} snapshot has no header.")
            return [dict(row) for row in reader]
    except OSError:
        raise SnapshotError(f"{env_name} snapshot could not be read.") from None


def _text(row: dict[str, str], field: str) -> str | None:
    value = row.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _price(row: dict[str, str], field: str) -> Decimal | None:
    value = _text(row, field)
    if value is None:
        return None
    try:
        price = Decimal(value)
    except InvalidOperation:
        return None
    return price if price.is_finite() and price > 0 else None


def _stats(values: list[Decimal], currency: str) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "sample_count": len(values),
        "minimum": float(min(values)),
        "median": float(median(values)),
        "maximum": float(max(values)),
        "currency": currency,
        "statistic": "unweighted_median_of_snapshot_rows",
    }


_LENGTH_TO_METRES = {"m": Decimal("1"), "meter": Decimal("1"), "metre": Decimal("1"),
                     "yd": Decimal("0.9144"), "yard": Decimal("0.9144")}
_MASS_TO_KG = {"kg": Decimal("1"), "g": Decimal("0.001")}


def _normalize_unit(price: Decimal, unit: str) -> tuple[Decimal, str, Decimal] | None:
    key = unit.strip().casefold()
    if key in _LENGTH_TO_METRES:
        factor = _LENGTH_TO_METRES[key]
        return price / factor, "per_m", factor
    if key in _MASS_TO_KG:
        factor = _MASS_TO_KG[key]
        return price / factor, "per_kg", factor
    return None


def analyze_fabric_snapshot(product: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep public price grades and unlike specs separate; normalize only known units."""
    rows = _read_rows("FABRIC_PRICE_SNAPSHOT_PATH")
    material = product.get("material")
    selected = [row for row in rows if _authorized(row) and material and _same(_text(row, "material"), material)]
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in selected:
        price = _price(row, "price")
        currency, unit = _text(row, "currency"), _text(row, "unit")
        if price is None or not currency or not unit or not _text(row, "source_url") or not _text(row, "collected_at"):
            continue
        grade = _text(row, "source_grade")
        if grade not in {"transaction", "supplier_quote", "public_sale", "market_reference"}:
            continue
        key = (grade, currency.upper(), unit.casefold(), _text(row, "composition") or "",
               _text(row, "construction") or "", _text(row, "weight_gsm") or "",
               _text(row, "width") or "", _text(row, "color") or "",
               _text(row, "moq") or "", _text(row, "quantity_tier") or "",
               _text(row, "shipping_tax_included") or "")
        groups.setdefault(key, []).append(row)
    results = []
    for key, group in groups.items():
        grade, currency, original_unit, composition, construction, gsm, width, color, moq, tier, shipping = key
        prices = [_price(row, "price") for row in group]
        amounts = [p for p in prices if p is not None]
        normalized = [_normalize_unit(p, original_unit) for p in amounts]
        normalized = [entry for entry in normalized if entry is not None]
        results.append({
            "material": material, "composition": composition or None, "construction": construction or None,
            "weight_gsm": gsm or None, "width": width or None, "color": color or None,
            "moq": moq or None, "quantity_tier": tier or None, "shipping_tax_included": shipping or None,
            "source_grade": grade, "original_unit": original_unit, "currency": currency,
            "original_price_stats": _stats(amounts, currency),
            "normalized_unit": normalized[0][1] if normalized and len(normalized) == len(amounts) else None,
            "normalized_conversion_factor_to_original": normalized[0][2] if normalized and len(normalized) == len(amounts) else None,
            "normalized_price_stats": _stats([n[0] for n in normalized], currency) if normalized and len(normalized) == len(amounts) else None,
            "sample_count": len(group),
            "sample_quality": "insufficient" if len(group) < 3 else "usable_descriptive_sample",
            "sources": [{"supplier": _text(row, "supplier"), "source_url": _text(row, "source_url"),
                         "collected_at": _text(row, "collected_at"), "quote_date": _text(row, "quote_date"),
                         "valid_until": _text(row, "valid_until"), "moq": _text(row, "moq"),
                         "quantity_tier": _text(row, "quantity_tier"), "shipping_tax_included": _text(row, "shipping_tax_included"),
                         "original_price": float(_price(row, "price")) if _price(row, "price") else None}
                        for row in group],
            "limitations": (["Reference price is not a confirmed production purchase cost."] if grade in {"public_sale", "market_reference"} else []),
        })
    return results
