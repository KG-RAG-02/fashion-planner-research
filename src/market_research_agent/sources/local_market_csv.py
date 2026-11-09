"""Permission-gated adapter for externally obtained market-data CSV files.

This adapter performs no network requests. The caller is responsible for
obtaining the file lawfully and providing its source/permission manifest.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.models import (
    AgentError,
    EvidenceRecord,
    FindingKind,
    MarketResearchContext,
    PermissionStatus,
    ResearchFinding,
    ResultStatus,
    SourceReport,
)
from ..domain.sales import MarketDataResult, MarketObservation, MissingData

MAX_MANIFEST_BYTES = 256 * 1024
MAX_CSV_BYTES = 50 * 1024 * 1024
MAX_OBSERVATIONS = 100_000
REQUIRED_COLUMNS = {"observation_id", "metric_name", "measure_type", "value", "unit", "evidence_id"}


class LocalMarketCsvSource:
    """Load a provenance-rich market CSV only when permission is authorized."""

    def __init__(self, observations_path: Path, manifest_path: Path) -> None:
        self.observations_path = observations_path
        self.manifest_path = manifest_path

    def research(self, context: MarketResearchContext) -> SourceReport:
        try:
            manifest = self._read_manifest()
        except FileNotFoundError:
            return self._missing("source_not_configured", "Market CSV or source manifest was not found.")
        except (OSError, ValueError, json.JSONDecodeError):
            return self._invalid("The market source manifest is unreadable or invalid.")

        permission = manifest.get("permission_status")
        if permission != "authorized":
            reason = "access_denied" if permission == "denied" else "permission_not_confirmed"
            message = "Market-data permission is denied." if permission == "denied" else "Market-data permission is not confirmed as authorized."
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=(message,),
                errors=(AgentError(
                    "MARKET_SOURCE_ACCESS_DENIED" if permission == "denied" else "MARKET_SOURCE_PERMISSION_UNCONFIRMED",
                    message,
                ),),
                market_data=MarketDataResult(
                    status="error",
                    errors=(message,),
                    missing_data=(MissingData("market_data", reason, message, manifest.get("source_id")),),
                ),
            )

        required_manifest = ("source_id", "source_type", "source_name", "manifest_id")
        if any(not isinstance(manifest.get(key), str) or not manifest[key].strip() for key in required_manifest):
            return self._invalid("The market source manifest is missing required source identity fields.")

        try:
            observations, evidence, query_search_available = self._read_observations(manifest, context)
        except FileNotFoundError:
            return self._missing("source_not_configured", "Market CSV or source manifest was not found.")
        except (OSError, ValueError, csv.Error, UnicodeError):
            return self._invalid("The market CSV could not be read or contains invalid observation fields.")

        if not observations:
            reason = "no_matching_records"
            message = "The configured market source returned no observations matching the requested category and scope."
            if context.requested_query and not query_search_available:
                message += " The CSV has no search_text column, so the raw query could not be applied to row text."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message,),
                market_data=MarketDataResult(
                    status="no_results",
                    limitations=(message,),
                    missing_data=(MissingData("market_data", reason, message, manifest["source_id"]),),
                ),
            )

        findings = tuple(
            ResearchFinding(
                finding_id=f"market-observation:{item.observation_id}",
                topic=item.metric_name,
                statement=(
                    f"Source observation: {item.metric_name} = {item.value:g} {item.unit}; "
                    f"category={item.category or 'unspecified'}, geography={item.geography or 'unspecified'}, "
                    f"channel={item.channel or 'unspecified'}, period="
                    f"{item.period_start or 'unspecified'} to {item.period_end or 'unspecified'}, "
                    f"as_of={item.as_of or 'unspecified'}."
                ),
                kind=FindingKind.FACT,
                evidence_ids=item.evidence_ids,
            )
            for item in observations
        )
        limitations = tuple(dict.fromkeys(limitation for item in observations for limitation in _scope_limitations(item, context)))
        if context.requested_query and not query_search_available:
            limitations = (*limitations, "The raw user query was passed to the source but not applied to CSV row text because the file has no search_text column.")
        market_status = "partial" if limitations else "ok"
        return SourceReport(
            status=ResultStatus.PARTIAL if limitations else ResultStatus.OK,
            findings=findings,
            evidence=tuple(evidence.values()),
            market_data=MarketDataResult(
                status=market_status,
                observations=tuple(observations),
                limitations=limitations,
            ),
        )

    def _read_manifest(self) -> dict[str, Any]:
        path = self.manifest_path.expanduser()
        if not path.is_file():
            raise FileNotFoundError
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("manifest too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("manifest root must be an object")
        return value

    def _read_observations(
        self, manifest: dict[str, Any], context: MarketResearchContext
    ) -> tuple[list[MarketObservation], dict[str, EvidenceRecord], bool]:
        path = self.observations_path.expanduser()
        if not path.is_file():
            raise FileNotFoundError
        if path.stat().st_size > MAX_CSV_BYTES:
            raise ValueError("market CSV too large")

        rows: list[MarketObservation] = []
        evidence: dict[str, EvidenceRecord] = {}
        seen_observation_ids: set[str] = set()
        requested_metrics = {value.casefold() for value in context.requested_metrics}
        query_search_available = False
        fetched_at = manifest.get("fetched_at") or datetime.now(timezone.utc).isoformat()
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not REQUIRED_COLUMNS.issubset(set(reader.fieldnames or ())):
                raise ValueError("market CSV has missing required columns")
            query_search_available = "search_text" in set(reader.fieldnames or ())
            for row_number, row in enumerate(reader, start=1):
                if row_number > MAX_OBSERVATIONS:
                    raise ValueError("market CSV row limit exceeded")
                category = _optional(row, "category")
                metric_name = _required(row, "metric_name")
                if requested_metrics and metric_name.casefold() not in requested_metrics:
                    continue
                if context.requested_query and query_search_available:
                    row_search_text = (row.get("search_text") or "").casefold()
                    if context.requested_query.casefold() not in row_search_text:
                        continue
                if context.requested_category and category and category.casefold() != context.requested_category.casefold():
                    continue
                geography = _optional(row, "geography")
                channel = _optional(row, "channel")
                if context.requested_geography and geography and geography.casefold() != context.requested_geography.casefold():
                    continue
                if context.requested_channel and channel and channel.casefold() != context.requested_channel.casefold():
                    continue

                value = float((row.get("value") or "").strip())
                if not math.isfinite(value):
                    raise ValueError("market value must be finite")
                evidence_id = _required(row, "evidence_id")
                observation = MarketObservation(
                    observation_id=_required(row, "observation_id"),
                    metric_name=metric_name,
                    measure_type=_required(row, "measure_type"),
                    value=value,
                    unit=_required(row, "unit"),
                    evidence_ids=(evidence_id,),
                    currency=_optional(row, "currency"),
                    metric_definition=_optional(row, "metric_definition"),
                    period_start=_optional(row, "period_start"),
                    period_end=_optional(row, "period_end"),
                    period_grain=_optional(row, "period_grain"),
                    as_of=_optional(row, "as_of"),
                    fetched_at=fetched_at,
                    geography=geography,
                    channel=channel,
                    population=_optional(row, "population"),
                    product_id=_optional(row, "product_id"),
                    category=category,
                    source_category=_optional(row, "source_category"),
                    product_mapping_id=_optional(row, "product_mapping_id"),
                    product_mapping_version=_optional(row, "product_mapping_version"),
                    calculation_reference=_optional(row, "calculation_reference"),
                )
                if observation.observation_id in seen_observation_ids:
                    raise ValueError("duplicate observation_id")
                seen_observation_ids.add(observation.observation_id)
                rows.append(observation)
                evidence.setdefault(evidence_id, EvidenceRecord(
                    evidence_id=evidence_id,
                    source_id=manifest["source_id"],
                    source_type=manifest["source_type"],
                    source_name=manifest["source_name"],
                    locator=manifest.get("locator"),
                    published_at=manifest.get("published_at"),
                    observed_at=_optional(row, "as_of") or _optional(row, "period_end"),
                    fetched_at=fetched_at,
                    period_start=_optional(row, "period_start"),
                    period_end=_optional(row, "period_end"),
                    as_of=_optional(row, "as_of"),
                    geography=geography,
                    channel=channel,
                    product_mapping=_optional(row, "product_mapping_version") or _optional(row, "product_mapping_id"),
                    unit=observation.unit,
                    currency=observation.currency,
                    metric_definition=observation.metric_definition,
                    calculation_reference=observation.calculation_reference,
                    permission_status=PermissionStatus.AUTHORIZED,
                    manifest_id=manifest["manifest_id"],
                ))
        return rows, evidence, query_search_available

    @staticmethod
    def _missing(reason: str, message: str) -> SourceReport:
        missing = MissingData("market_data", reason, message)
        return SourceReport(
            status=ResultStatus.NO_RESULTS,
            limitations=(message,),
            market_data=MarketDataResult("no_results", limitations=(message,), missing_data=(missing,)),
        )

    @staticmethod
    def _invalid(message: str) -> SourceReport:
        missing = MissingData("market_data", "invalid_data", message)
        return SourceReport(
            status=ResultStatus.ERROR,
            limitations=(message,),
            errors=(AgentError("INVALID_MARKET_SOURCE_INPUT", message),),
            market_data=MarketDataResult("error", errors=(message,), missing_data=(missing,)),
        )


def _required(row: dict[str, str], key: str) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ValueError("required market field is blank")
    return value


def _optional(row: dict[str, str], key: str) -> str | None:
    value = (row.get(key) or "").strip()
    return value or None


def _scope_limitations(
    observation: MarketObservation, context: MarketResearchContext
) -> tuple[str, ...]:
    limitations: list[str] = []
    if context.requested_category and observation.category is None:
        limitations.append("A market observation has no category mapping; it is not safely aligned to the requested category.")
    if context.requested_geography and observation.geography is None:
        limitations.append("A market observation has no geography; market scope cannot be confirmed.")
    if context.requested_channel and observation.channel is None:
        limitations.append("A market observation has no channel; online scope cannot be confirmed.")
    if not (observation.period_start and observation.period_end) and not observation.as_of:
        limitations.append("A market observation has neither a complete period nor an as_of date.")
    if observation.category and not observation.product_mapping_version:
        limitations.append("A source category is present without a product mapping version.")
    return tuple(limitations)
