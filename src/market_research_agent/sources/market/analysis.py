"""Deterministic calculations for compatible structured market series."""

from __future__ import annotations

from datetime import date, timedelta

from ...domain.calculations import CalculationRecord
from ...domain.sales import MarketObservation


FORMULA_VERSION = "1.0"


def calculate_market_series_change(
    baseline: MarketObservation,
    current: MarketObservation,
) -> tuple[CalculationRecord, ...]:
    """Calculate a period change only for a compatible, ordered market series.

    This function never accesses free text, embeddings, or generated estimates.
    Every formula points to the two exact input observation and Evidence IDs.
    """
    mismatches = _series_mismatches(baseline, current)
    if mismatches:
        raise ValueError("Market observations are not comparable: " + ", ".join(mismatches))

    baseline_end = date.fromisoformat(baseline.period_end or "")
    current_start = date.fromisoformat(current.period_start or "")
    if current_start <= baseline_end:
        raise ValueError("Market series periods must be ordered and non-overlapping.")
    if current_start != baseline_end + timedelta(days=1):
        raise ValueError("Market series periods are not contiguous; the change would span an unobserved gap.")

    input_observation_ids = (baseline.observation_id, current.observation_id)
    evidence_ids = tuple(dict.fromkeys((*baseline.evidence_ids, *current.evidence_ids)))
    period_start = baseline.period_start
    period_end = current.period_end
    base = float(baseline.value)
    latest = float(current.value)
    records: list[CalculationRecord] = []

    if baseline.measure_type == "aggregate_index":
        records.append(CalculationRecord(
            calculation_id=f"index-points:{baseline.observation_id}:{current.observation_id}",
            metric_name=f"{current.metric_name}_point_change",
            value=latest - base,
            unit="index_points",
            formula_id="index-point-change",
            formula_version=FORMULA_VERSION,
            input_observation_ids=input_observation_ids,
            evidence_ids=evidence_ids,
            period_start=period_start,
            period_end=period_end,
            geography=current.geography,
            channel=current.channel,
            category=current.category,
            definition="Current index value minus baseline index value; not a currency amount.",
        ))
        percent_formula = "index-relative-change-percent"
    else:
        percent_formula = "series-relative-change-percent"

    if base != 0:
        records.append(CalculationRecord(
            calculation_id=f"relative-change:{baseline.observation_id}:{current.observation_id}",
            metric_name=f"{current.metric_name}_relative_change_percent",
            value=(latest / base - 1) * 100,
            unit="percent",
            formula_id=percent_formula,
            formula_version=FORMULA_VERSION,
            input_observation_ids=input_observation_ids,
            evidence_ids=evidence_ids,
            period_start=period_start,
            period_end=period_end,
            geography=current.geography,
            channel=current.channel,
            category=current.category,
            definition="(current value / baseline value - 1) × 100 for the same declared source series.",
        ))
    return tuple(records)


def _series_mismatches(baseline: MarketObservation, current: MarketObservation) -> tuple[str, ...]:
    dimensions = {
        "metric_name": (baseline.metric_name, current.metric_name),
        "measure_type": (baseline.measure_type, current.measure_type),
        "unit": (baseline.unit, current.unit),
        "geography": (baseline.geography, current.geography),
        "channel": (baseline.channel, current.channel),
        "category": (baseline.category, current.category),
        "population": (baseline.population, current.population),
        "product_mapping_id": (baseline.product_mapping_id, current.product_mapping_id),
        "product_mapping_version": (baseline.product_mapping_version, current.product_mapping_version),
        "period_grain": (baseline.period_grain, current.period_grain),
        "metric_definition": (baseline.metric_definition, current.metric_definition),
    }
    mismatches = [
        f"{name}:unknown" if not left or not right else f"{name}:mismatch"
        for name, (left, right) in dimensions.items()
        if not left or not right or left.casefold() != right.casefold()
    ]
    if baseline.currency is None and current.currency is None and baseline.measure_type == "aggregate_index":
        pass  # Currency is not applicable to a pure index series.
    elif not baseline.currency or not current.currency:
        mismatches.append("currency:unknown")
    elif baseline.currency.casefold() != current.currency.casefold():
        mismatches.append("currency:mismatch")
    if not baseline.period_start or not baseline.period_end or not current.period_start or not current.period_end:
        mismatches.append("period:incomplete")
    if baseline.measure_type not in {"aggregate_index", "market_estimate", "transaction_actual"}:
        mismatches.append("measure_type:not_calculable")
    return tuple(mismatches)
