"""Fixture loading and source metadata helpers for the local prototype."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from .config import FIXTURE, hm_analysis_path


def load_fixture() -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source_ids = {source["source_id"] for source in data["sources"]}
    for key in ("products", "sales_profiles", "inventory", "feedback", "documents"):
        for row in data[key]:
            if row["source_id"] not in source_ids:
                raise ValueError(f"Fixture row has unknown source: {row['source_id']}")
    return data


def get_hm_analysis() -> dict[str, Any] | None:
    """Return the separate real Kaggle analysis report when the pipeline ran."""
    path = hm_analysis_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def get_hm_planning_analysis() -> dict[str, Any] | None:
    """Return article-level category planning analysis when generated locally."""
    path = hm_analysis_path().with_name("hm_planning_analysis.json")
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _source_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["source_id"]: row for row in data["sources"]}


def _weeks_for_profile(profile: dict[str, Any]) -> list[str]:
    start = date(2025, 12, 1)
    return [(start + timedelta(days=7 * index)).isoformat() for index in range(len(profile["weekly_units"]))]


def _evidence(source_id: str, record_id: str, sources: dict[str, dict[str, Any]], label: str) -> dict[str, Any]:
    source = sources[source_id]
    return {
        "label": label,
        "record_id": record_id,
        "source_id": source_id,
        "source_name": source["name"],
        "period": source["period"],
        "version": source["version"],
        "permission": source["permission"],
        "as_of": source["as_of"],
    }
