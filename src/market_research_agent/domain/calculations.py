"""Provenance records for deterministic, replayable metric calculations."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CalculationRecord:
    calculation_id: str
    metric_name: str
    value: int | float
    unit: str
    formula_id: str
    formula_version: str
    input_observation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    period_start: str | None = None
    period_end: str | None = None
    geography: str | None = None
    channel: str | None = None
    category: str | None = None
    definition: str | None = None

    def __post_init__(self) -> None:
        required = (self.calculation_id, self.metric_name, self.unit, self.formula_id, self.formula_version)
        if any(not value.strip() for value in required):
            raise ValueError("Calculation identity, unit, and formula version are required.")
        if not self.input_observation_ids or not self.evidence_ids:
            raise ValueError("Calculations require input observation and Evidence IDs.")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("Calculation value must be finite and numeric.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
