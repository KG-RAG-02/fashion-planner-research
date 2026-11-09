"""Sales-specific domain records owned by Project_07.

These records describe observations and their scope. They do not imply demand
forecasts, customer preference, or comparable metrics across incompatible
periods, channels, units, currencies, or product mappings.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SalesObservation:
    """One evidence-linked sales observation at a declared grain."""

    observation_id: str
    metric_name: str
    value: int | float
    unit: str
    evidence_ids: tuple[str, ...]
    product_id: str | None = None
    category: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    as_of: str | None = None
    geography: str | None = None
    channel: str | None = None
    currency: str | None = None
    metric_definition: str | None = None
    product_mapping: str | None = None

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or not self.metric_name.strip() or not self.unit.strip():
            raise ValueError("observation_id, metric_name, and unit are required.")
        if not self.evidence_ids or any(not item.strip() for item in self.evidence_ids):
            raise ValueError("Each sales observation requires traceable Evidence IDs.")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("Sales observation value must be numeric.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SalesDataResult:
    """Sales domain payload within the Project_07 market result."""

    status: str
    observations: tuple[SalesObservation, ...] = ()
    limitations: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    follow_up_questions: tuple[str, ...] = ()
    missing_data: tuple["MissingData", ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"ok", "partial", "no_results", "error"}:
            raise ValueError("Unsupported sales result status.")
        if self.status == "no_results" and self.observations:
            raise ValueError("no_results sales payload must not contain observations.")
        if self.status == "error" and self.observations:
            raise ValueError("error sales payload must not contain observations.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observations": [item.to_dict() for item in self.observations],
            "limitations": list(self.limitations),
            "errors": list(self.errors),
            "follow_up_questions": list(self.follow_up_questions),
            "missing_data": [item.to_dict() for item in self.missing_data],
        }


@dataclass(frozen=True, slots=True)
class MissingData:
    """Reason a requested field or metric was not returned; never a zero value."""

    field: str
    reason: str
    message: str
    source_id: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        allowed = {
            "source_not_configured", "no_matching_records", "not_available_from_source",
            "access_denied", "mapping_failed", "invalid_data", "source_error", "out_of_scope",
        }
        if not self.field.strip() or not self.message.strip() or self.reason not in allowed:
            raise ValueError("MissingData requires a field, supported reason, and message.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """One external market statistic/estimate with declared scope and Evidence."""

    observation_id: str
    metric_name: str
    measure_type: str
    value: int | float
    unit: str
    evidence_ids: tuple[str, ...]
    currency: str | None = None
    metric_definition: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    period_grain: str | None = None
    as_of: str | None = None
    fetched_at: str | None = None
    geography: str | None = None
    channel: str | None = None
    population: str | None = None
    product_id: str | None = None
    category: str | None = None
    source_category: str | None = None
    product_mapping_id: str | None = None
    product_mapping_version: str | None = None
    calculation_reference: str | None = None

    def __post_init__(self) -> None:
        allowed = {"transaction_actual", "aggregate_index", "market_estimate", "forecast", "search_interest"}
        if not self.observation_id.strip() or not self.metric_name.strip() or not self.unit.strip():
            raise ValueError("observation_id, metric_name, and unit are required.")
        if self.measure_type not in allowed:
            raise ValueError("Unsupported market observation measure_type.")
        if not self.evidence_ids or any(not item.strip() for item in self.evidence_ids):
            raise ValueError("Each market observation requires Evidence IDs.")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("Market observation value must be finite and numeric.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarketDataResult:
    status: str
    observations: tuple[MarketObservation, ...] = ()
    limitations: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    missing_data: tuple[MissingData, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"ok", "partial", "no_results", "error"}:
            raise ValueError("Unsupported market data status.")
        if self.status in {"no_results", "error"} and self.observations:
            raise ValueError(f"{self.status} market data must not contain observations.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observations": [item.to_dict() for item in self.observations],
            "limitations": list(self.limitations),
            "errors": list(self.errors),
            "missing_data": [item.to_dict() for item in self.missing_data],
        }


@dataclass(frozen=True, slots=True)
class UnitEconomicsCalculation:
    calculation_id: str
    metric_name: str
    value: int | float
    unit: str
    formula_id: str
    formula_version: str
    input_evidence_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    currency: str | None = None
    calculation_reference: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    as_of: str | None = None
    product_id: str | None = None
    category: str | None = None
    channel: str | None = None
    currency_basis: str | None = None
    definition: str | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.calculation_id, self.metric_name, self.unit, self.formula_id, self.formula_version)):
            raise ValueError("Calculation identity, metric, unit, and formula version are required.")
        if not self.input_evidence_ids or not self.evidence_ids:
            raise ValueError("Unit-economics calculations require input and result Evidence IDs.")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("Calculation value must be finite and numeric.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UnitEconomicsResult:
    status: str
    calculations: tuple[UnitEconomicsCalculation, ...] = ()
    limitations: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    missing_data: tuple[MissingData, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"ok", "partial", "no_results", "error"}:
            raise ValueError("Unsupported unit-economics status.")
        if self.status in {"no_results", "error"} and self.calculations:
            raise ValueError(f"{self.status} unit economics must not contain calculations.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "calculations": [item.to_dict() for item in self.calculations],
            "limitations": list(self.limitations),
            "errors": list(self.errors),
            "missing_data": [item.to_dict() for item in self.missing_data],
        }


def peer_percentile_scores(observations: Mapping[str, float | None]) -> dict[str, float | None]:
    """Compare declared like-for-like sales observations using midranks.

    The caller is responsible for grouping records with matching category,
    period, channel, unit and metric definition before ranking them.
    """
    valid = [(key, value) for key, value in observations.items()
             if value is not None and math.isfinite(value)]
    ordered = sorted(valid, key=lambda item: (item[1], item[0]))
    result: dict[str, float | None] = {key: None for key in observations}
    index = 0
    while index < len(ordered):
        end = index + 1
        value = ordered[index][1]
        while end < len(ordered) and ordered[end][1] == value:
            end += 1
        midrank = ((index + 1) + end) / 2
        score = 50.0 if len(ordered) == 1 else 100 * (midrank - 1) / (len(ordered) - 1)
        for key, _ in ordered[index:end]:
            result[key] = round(score, 2)
        index = end
    return result
