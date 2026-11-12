"""Opt-in local demo source backed only by test-only generated fixtures."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ...domain.models import (
    EvidenceRecord, FindingKind, MarketResearchContext, PermissionStatus,
    ResearchFinding, ResultStatus, SourceReport,
)
from ...domain.sales import MarketDataResult, MarketObservation, SalesDataResult

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[4] / "examples/query_coverage/generated"
LIMITATION = (
    "SYNTHETIC TEST DATA ONLY: generated scenario values are not actual H&M/VOGUE sales, "
    "observed market data, forecasts, or evidence for business decisions."
)
_DIMENSION_FIELDS = (
    "source_id", "source_schema_version", "metric_name", "metric_definition", "measure_type",
    "unit", "currency", "period_grain", "geography", "channel", "gender", "category",
    "season", "season_year", "population", "product_mapping_id", "product_mapping_version",
    "source_category", "vintage_id", "attributes",
)
_MAX_SERIES_PER_DOMAIN = 32
_MAX_MONTHS_PER_SERIES = 24


def _extract_scope(context: MarketResearchContext) -> dict[str, Any]:
    request_context = context.planning_request.get("context")
    request_context = request_context if isinstance(request_context, dict) else {}
    query = context.requested_query or context.planning_request.get("query") or context.planning_request.get("original_query") or ""
    query = query if isinstance(query, str) else ""
    category = context.requested_category
    if not category:
        value = request_context.get("category")
        category = value.get("value") if isinstance(value, dict) else value
    if not category:
        for pattern, canonical in ((r"knit|니트", "knitwear"), (r"footwear|shoes?|신발", "footwear"), (r"outerwear|아우터", "outerwear")):
            if re.search(pattern, query, re.IGNORECASE):
                category = canonical
                break

    gender = request_context.get("gender") or request_context.get("customer_group")
    if not gender:
        for pattern, canonical in ((r"men|남성|남자", "men"), (r"women|여성|여자", "women"), (r"unisex|유니섹스", "unisex")):
            if re.search(pattern, query, re.IGNORECASE):
                gender = canonical
                break
    if isinstance(gender, str):
        lowered = gender.casefold()
        if lowered in {"male", "mens", "men", "남성", "남자"}:
            gender = "men"
        elif lowered in {"female", "womens", "women", "여성", "여자"}:
            gender = "women"
        elif lowered in {"unisex", "유니섹스"}:
            gender = "unisex"

    season = request_context.get("season")
    season_year = request_context.get("season_year")
    if season is None:
        if re.search(r"spring|봄", query, re.IGNORECASE):
            season = "SS"
        elif re.search(r"summer|여름", query, re.IGNORECASE):
            season = "SS"
        elif re.search(r"winter|겨울|fall|autumn|가을", query, re.IGNORECASE):
            season = "FW"
    if season is not None:
        season = str(season).upper()
        season = {"SPRING": "SS", "SUMMER": "SS", "WINTER": "FW", "FALL": "FW", "AUTUMN": "FW"}.get(season, season)
    if season_year is None:
        match = re.search(r"(20\d{2})", query)
        season_year = int(match.group(1)) if match else None
    elif isinstance(season_year, str) and season_year.isdigit():
        season_year = int(season_year)

    period_start = request_context.get("period_start")
    period_end = request_context.get("period_end")
    requested_period = context.requested_period
    if requested_period and (period_start is None or period_end is None):
        dates = re.findall(r"20\d{2}-\d{2}", requested_period)
        if len(dates) == 1:
            period_start = period_end = dates[0]
        elif len(dates) >= 2:
            period_start, period_end = dates[0], dates[1]
    if period_start is None and season_year is not None and season in {"SS", "FW"}:
        if re.search(r"spring|봄", query, re.IGNORECASE):
            period_start, period_end = f"{season_year}-03", f"{season_year}-05"
        elif re.search(r"summer|여름", query, re.IGNORECASE):
            period_start, period_end = f"{season_year}-06", f"{season_year}-08"
        elif season == "FW":
            period_start, period_end = f"{season_year}-12", f"{season_year + 1}-02"
        else:
            period_start, period_end = f"{season_year}-03", f"{season_year}-05"

    color_exclude = request_context.get("color_exclude") or request_context.get("colors_exclude") or []
    if not isinstance(color_exclude, list):
        color_exclude = []
    normalized_excludes = {str(item).casefold() for item in color_exclude}
    if re.search(r"(검정|블랙|black).{0,12}(아닌|제외|빼고|except|excluding|exclude|not)", query, re.IGNORECASE):
        normalized_excludes.add("black")
    if re.search(r"(not|without|except|excluding|exclude).{0,12}(black|검정|블랙)", query, re.IGNORECASE):
        normalized_excludes.add("black")
    return {
        "category": category if isinstance(category, str) else None,
        "gender": gender if isinstance(gender, str) else None,
        "season": season,
        "season_year": season_year if isinstance(season_year, int) else None,
        "period_start": period_start,
        "period_end": period_end,
        "geography": context.requested_geography,
        "channel": context.requested_channel,
        "colors_exclude": sorted(normalized_excludes),
        "metrics": set(context.requested_metrics),
        "query": query,
    }


class SyntheticQueryCoverageSource:
    """Demo-only deterministic filter; never represents a real source."""

    def __init__(self, fixture_dir: Path = DEFAULT_FIXTURE_DIR,
                 bundle_loader: Callable[[], dict[str, Any]] | None = None) -> None:
        self.fixture_dir = fixture_dir
        self.bundle_loader = bundle_loader

    def research(self, context: MarketResearchContext) -> SourceReport:
        try:
            from ...synthetic_rag import load_fixture_bundle

            fixture = (self.bundle_loader() if self.bundle_loader is not None
                       else load_fixture_bundle(query_coverage_root=self.fixture_dir))
            market_rows = fixture["observations"]["market_like"]
            sales_rows = fixture["observations"]["sales_like"]
        except (OSError, ValueError):
            return SourceReport(
                status=ResultStatus.ERROR,
                errors=(), limitations=("Synthetic demo fixture is missing or invalid; no generated values were used.", LIMITATION),
                sales_data=SalesDataResult("error", errors=("Synthetic demo fixture is missing or invalid.",)),
                market_data=MarketDataResult("error", errors=("Synthetic demo fixture is missing or invalid.",)),
            )
        fallback_marker = fixture.get("_source_fallback")
        fallback_limitation = (
            "Cloud synthetic data was unavailable or unconfigured; an allowlisted local synthetic copy was used."
            if fallback_marker in {
                "cloud_r2_unavailable_local_synthetic_copy",
                "cloud_r2_not_configured_local_synthetic_copy",
            } else None
        )
        scope = _extract_scope(context)
        if scope["metrics"]:
            market_rows = [row for row in market_rows if row.get("metric_name") in scope["metrics"]]
            sales_rows = [row for row in sales_rows if row.get("metric_name") in scope["metrics"]]
        matching_market = self._filter(market_rows, scope)
        matching_sales = self._filter(sales_rows, scope)
        if not matching_market and not matching_sales:
            message = "No generated fixture rows match this exact scope; values were not inferred or extrapolated."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message, LIMITATION, *((fallback_limitation,) if fallback_limitation else ())),
                sales_data=SalesDataResult("no_results", limitations=(message, LIMITATION)),
                market_data=MarketDataResult("no_results", limitations=(message, LIMITATION)),
            )

        findings = []
        limitations = [LIMITATION, *((fallback_limitation,) if fallback_limitation else ())]
        market_observations: list[MarketObservation] = []
        market_evidence: dict[str, EvidenceRecord] = {}
        applied_scope_note = (
            f"Applied canonical scope: {scope['gender']} {scope['category']}, {scope['period_start']}–{scope['period_end']}, "
            f"{scope['geography']}/{scope['channel']}, excluded colors={','.join(scope['colors_exclude']) or 'none'}."
        )
        if matching_sales:
            series, omitted_groups, omitted_months, duplicate_months = self._group_monthly_series(matching_sales)
            findings.append(ResearchFinding(
                finding_id=f"synthetic-sales:{context.request_id}", topic="synthetic_sales_demo",
                statement=("[SYNTHETIC TEST DATA] " + applied_scope_note + " Retailer-like item-unit scenario series, kept separate by geography, channel, population, category, mapping, and other declared dimensions: "
                           + " | ".join(item["summary"] for item in series)
                           + ". These values are generated fixtures, not Kaggle, H&M internal, or observed company sales."),
                kind=FindingKind.PROPOSAL,
            ))
            if omitted_groups or omitted_months:
                limitations.append(f"Sales-like display was capped: omitted {omitted_groups} compatible dimension groups and {omitted_months} monthly values. No omitted groups were combined.")
            if duplicate_months:
                limitations.append(f"Sales-like display omitted {duplicate_months} months with duplicate declared grain rows; those values require source QA.")
        if matching_market:
            series, omitted_groups, omitted_months, duplicate_months = self._group_monthly_series(matching_market)
            market_observations, market_evidence = self._structured_market_output(
                matching_market, series,
            )
            findings.append(ResearchFinding(
                finding_id=f"synthetic-market:{context.request_id}", topic="synthetic_market_demo",
                statement=("[SYNTHETIC TEST DATA] " + applied_scope_note + " Generated scenario market-value series, kept separate by geography, channel, population, category, mapping, and other declared dimensions: "
                           + " | ".join(item["summary"] for item in series)
                           + ". This is not an observed market estimate, forecast, or business evidence."),
                kind=FindingKind.PROPOSAL,
            ))
            if omitted_groups or omitted_months:
                limitations.append(f"Market-like display was capped: omitted {omitted_groups} compatible dimension groups and {omitted_months} monthly values. No omitted groups were combined.")
            if duplicate_months:
                limitations.append(f"Market-like display omitted {duplicate_months} months with duplicate declared grain rows; those values require source QA.")
        partial_note = "Sales-like and market-like fixture values use different units and synthetic populations; they are not directly comparable."
        if not matching_sales or not matching_market:
            partial_note += " One domain has no matching fixture rows."
        return SourceReport(
            status=ResultStatus.PARTIAL,
            findings=tuple(findings),
            evidence=tuple(market_evidence.values()),
            limitations=(*limitations, partial_note, "The stable HTTP result schema 1.0 has no structured sales_data, market_data, synthetic marker, or chart artifact fields; the stable route carries labeled findings only. The unratified 1.3 proposal output includes structured market observations with test-only markers.", "Unscoped dimension series are kept separate; the stable route does not calculate adjacent-period changes."),
            sales_data=SalesDataResult("partial" if matching_sales else "no_results", limitations=(LIMITATION,)),
            market_data=MarketDataResult(
                "partial" if matching_market else "no_results",
                observations=tuple(market_observations),
                limitations=(LIMITATION,),
            ),
        )

    @staticmethod
    def _structured_market_output(
        rows: list[dict[str, Any]], series: list[dict[str, Any]],
    ) -> tuple[list[MarketObservation], dict[str, EvidenceRecord]]:
        """Materialize visible synthetic market rows with explicit test markers."""
        grouped_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            dimensions = {key: row.get(key) for key in _DIMENSION_FIELDS}
            group_key = json.dumps(dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            grouped_rows[(group_key, row["period_start"][:7])].append(row)

        observations: list[MarketObservation] = []
        evidence: dict[str, EvidenceRecord] = {}
        for item in series:
            dimensions = item["dimensions"]
            group_key = json.dumps(dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for month in item["monthly_values"]:
                candidates = grouped_rows.get((group_key, month), [])
                # The display path omits duplicate declared month grains. Keep
                # the structured output aligned with exactly those visible rows.
                if len(candidates) != 1:
                    continue
                row = candidates[0]
                evidence_ids = tuple(dict.fromkeys(row.get("evidence_ids", ())))
                if not evidence_ids:
                    continue
                source_id = str(row["source_id"])
                for evidence_id in evidence_ids:
                    evidence.setdefault(evidence_id, EvidenceRecord(
                        evidence_id=evidence_id,
                        source_id=source_id,
                        source_type="synthetic_market_fixture",
                        source_name="Synthetic query coverage market scenario",
                        locator=f"synthetic://query-coverage/{source_id}/{evidence_id}",
                        period_start=row.get("period_start"),
                        period_end=row.get("period_end"),
                        geography=row.get("geography"),
                        channel=row.get("channel"),
                        product_mapping=row.get("product_mapping_version"),
                        unit=row.get("unit"),
                        currency=row.get("currency"),
                        metric_definition=row.get("metric_definition"),
                        permission_status=PermissionStatus.SYNTHETIC_TEST_ONLY,
                        synthetic=True,
                        data_nature="synthetic_test",
                        operational_eligible=False,
                    ))
                observations.append(MarketObservation(
                    observation_id=str(row["observation_id"]),
                    metric_name=str(row["metric_name"]),
                    measure_type=str(row["measure_type"]),
                    value=float(row["value"]),
                    unit=str(row["unit"]),
                    evidence_ids=evidence_ids,
                    currency=row.get("currency"),
                    metric_definition=row.get("metric_definition"),
                    period_start=row.get("period_start"),
                    period_end=row.get("period_end"),
                    period_grain=row.get("period_grain"),
                    geography=row.get("geography"),
                    channel=row.get("channel"),
                    population=row.get("population"),
                    category=row.get("category"),
                    source_category=row.get("source_category"),
                    product_mapping_id=row.get("product_mapping_id"),
                    product_mapping_version=row.get("product_mapping_version"),
                    attributes=row.get("attributes"),
                    synthetic=True,
                    data_nature="synthetic_test",
                    permission_status="authorized_for_synthetic_testing_only",
                    operational_eligible=False,
                ))
        return observations, evidence

    @staticmethod
    def _filter(rows: list[dict[str, Any]], scope: dict[str, Any]) -> list[dict[str, Any]]:
        matches = []
        excluded = set(scope["colors_exclude"])
        for row in rows:
            if scope["category"] and row.get("category", "").casefold() != scope["category"].casefold():
                continue
            if scope["gender"] and row.get("gender", "").casefold() != scope["gender"].casefold():
                continue
            if scope["geography"] and row.get("geography", "").casefold() != scope["geography"].casefold():
                continue
            if scope["channel"] and row.get("channel", "").casefold() != scope["channel"].casefold():
                continue
            period = row.get("period_start", "")[:7]
            if scope["period_start"] and scope["period_end"] and not scope["period_start"] <= period <= scope["period_end"]:
                continue
            if scope["season"] and row.get("season") and row["season"].casefold() != scope["season"].casefold():
                continue
            if scope["season_year"] is not None and row.get("season_year") is not None and row["season_year"] != scope["season_year"]:
                continue
            color = str(row.get("attributes", {}).get("color", "")).casefold()
            if color in excluded:
                continue
            matches.append(row)
        return matches

    @staticmethod
    def _group_monthly_series(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int, int]:
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            dimensions = {key: row.get(key) for key in _DIMENSION_FIELDS}
            group_key = json.dumps(dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            group = groups.setdefault(group_key, {"dimensions": dimensions, "months": defaultdict(list)})
            month = row["period_start"][:7]
            group["months"][month].append(row)

        ordered = sorted(groups.items())
        visible = ordered[:_MAX_SERIES_PER_DOMAIN]
        result = []
        omitted_months = 0
        duplicate_months = 0
        for group_key, group in visible:
            months = sorted(group["months"].items())
            shown_months = months[:_MAX_MONTHS_PER_SERIES]
            omitted_months += len(months) - len(shown_months)
            duplicate_grains = sorted(month for month, month_rows in shown_months if len(month_rows) != 1)
            duplicate_months += len(duplicate_grains)
            dimensions = group["dimensions"]
            dimension_text = ", ".join(
                f"{key}={json.dumps(value, ensure_ascii=False, sort_keys=True)}"
                for key, value in dimensions.items() if value not in (None, {}, "")
            )
            usable_months = [(month, month_rows[0]) for month, month_rows in shown_months if len(month_rows) == 1]
            monthly_text = "; ".join(f"{month}={round(float(row['value']), 2):g} {dimensions['unit']}" for month, row in usable_months)
            note = f"{dimension_text}: {monthly_text}"
            if duplicate_grains:
                note += f" [omitted duplicate declared month grain: {','.join(duplicate_grains)}; source QA required]"
            result.append({"summary": note, "dimensions": dimensions,
                           "monthly_values": {month: float(row["value"]) for month, row in usable_months},
                           "duplicate_grains": duplicate_grains})
        return result, max(0, len(ordered) - len(visible)), omitted_months, duplicate_months
