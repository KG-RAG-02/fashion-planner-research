"""Opt-in local demo source backed only by test-only generated fixtures."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ...domain.models import FindingKind, MarketResearchContext, ResearchFinding, ResultStatus, SourceReport
from ...domain.sales import MarketDataResult, SalesDataResult

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[4] / "examples/query_coverage/generated"
LIMITATION = (
    "SYNTHETIC TEST DATA ONLY: generated scenario values are not actual H&M/VOGUE sales, "
    "observed market data, forecasts, or evidence for business decisions."
)


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

    def __init__(self, fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> None:
        self.fixture_dir = fixture_dir

    def research(self, context: MarketResearchContext) -> SourceReport:
        try:
            from ...synthetic_rag import load_fixture_bundle

            fixture = load_fixture_bundle(query_coverage_root=self.fixture_dir)
            market_rows = fixture["observations"]["market_like"]
            sales_rows = fixture["observations"]["sales_like"]
        except (OSError, ValueError):
            return SourceReport(
                status=ResultStatus.ERROR,
                errors=(), limitations=("Synthetic demo fixture is missing or invalid; no generated values were used.", LIMITATION),
                sales_data=SalesDataResult("error", errors=("Synthetic demo fixture is missing or invalid.",)),
                market_data=MarketDataResult("error", errors=("Synthetic demo fixture is missing or invalid.",)),
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
                limitations=(message, LIMITATION),
                sales_data=SalesDataResult("no_results", limitations=(message, LIMITATION)),
                market_data=MarketDataResult("no_results", limitations=(message, LIMITATION)),
            )

        findings = []
        applied_scope_note = (
            f"Applied canonical scope: {scope['gender']} {scope['category']}, {scope['period_start']}–{scope['period_end']}, "
            f"{scope['geography']}/{scope['channel']}, excluded colors={','.join(scope['colors_exclude']) or 'none'}."
        )
        if matching_sales:
            monthly = self._sum_by_month(matching_sales)
            findings.append(ResearchFinding(
                finding_id=f"synthetic-sales:{context.request_id}", topic="synthetic_sales_demo",
                statement=("[SYNTHETIC TEST DATA] " + applied_scope_note + " Retailer-like item-unit scenario totals by month: "
                           + "; ".join(f"{period}={value:g} item_units" for period, value in monthly.items())
                           + ". These values are generated fixtures, not Kaggle, H&M internal, or observed company sales."),
                kind=FindingKind.PROPOSAL,
            ))
        if matching_market:
            monthly = self._sum_by_month(matching_market)
            findings.append(ResearchFinding(
                finding_id=f"synthetic-market:{context.request_id}", topic="synthetic_market_demo",
                statement=("[SYNTHETIC TEST DATA] " + applied_scope_note + " Generated scenario market-value totals by month: "
                           + "; ".join(f"{period}={value:g} KRW_million" for period, value in monthly.items())
                           + ". This is not an observed market estimate, forecast, or business evidence."),
                kind=FindingKind.PROPOSAL,
            ))
        partial_note = "Sales-like and market-like fixture values use different units and synthetic populations; they are not directly comparable."
        if not matching_sales or not matching_market:
            partial_note += " One domain has no matching fixture rows."
        return SourceReport(
            status=ResultStatus.PARTIAL,
            findings=tuple(findings),
            limitations=(LIMITATION, partial_note, "The stable HTTP result schema 1.0 has no structured sales_data, market_data, synthetic marker, or chart artifact fields; values are therefore carried as explicitly labeled proposal findings only."),
            sales_data=SalesDataResult("partial" if matching_sales else "no_results", limitations=(LIMITATION,)),
            market_data=MarketDataResult("partial" if matching_market else "no_results", limitations=(LIMITATION,)),
        )

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
    def _sum_by_month(rows: list[dict[str, Any]]) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for row in rows:
            totals[row["period_start"][:7]] += float(row["value"])
        return {period: round(value, 2) for period, value in sorted(totals.items())}
