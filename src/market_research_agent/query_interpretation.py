"""Versioned, deterministic query interpretation for the local data boundary.

This parser extracts only high-confidence lexical hints. It does not claim to
understand arbitrary natural language or infer business facts.
"""

from __future__ import annotations

import re
from typing import Any

PARSER_VERSION = "project07-query-parser/1.1"

METRIC_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("purchase_rows", ("purchase rows", "purchase_rows", "구매 행 수", "구매행수", "구매행")),
    ("market_size", ("시장 규모", "시장규모", "market size", "market_size", "시장 크기")),
    ("retail_sales_index", ("판매 지수", "판매지수", "소매 지수", "소매판매지수", "retail sales index", "sales index")),
    ("revenue", ("매출", "매출액", "revenue", "sales revenue")),
    ("demand", ("수요", "demand", "forecast")),
    ("market_share", ("시장 점유율", "시장점유율", "market share")),
    ("price_band", ("가격대", "price band", "price range")),
)
CATEGORY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Outerwear", ("아우터", "outerwear", "겉옷", "외투")),
    ("Knitwear", ("니트웨어", "니트", "knitwear", "knit")),
    ("Footwear", ("신발", "풋웨어", "footwear", "shoes")),
)
GENDER_TERMS = (("women", ("여성", "여자", "women", "womens")),
                ("men", ("남성", "남자", "men", "mens")),
                ("unisex", ("유니섹스", "unisex")))


def _contains(text: str, term: str) -> bool:
    compact_text = re.sub(r"\s+", "", text.casefold())
    compact_term = re.sub(r"\s+", "", term.casefold())
    if re.fullmatch(r"[a-z0-9_ ]+", term.casefold()):
        return re.search(rf"(?<![a-z0-9]){re.escape(term.casefold())}(?![a-z0-9])", text.casefold()) is not None
    return compact_term in compact_text


def interpret_query(query: str | None) -> dict[str, Any]:
    """Return recognized intent/scope, unresolved ambiguity, and parser version."""
    text = (query or "").casefold()
    ambiguities: list[str] = []
    result: dict[str, Any] = {
        "parser_version": PARSER_VERSION,
        "intent": "unresolved",
        "category": None,
        "gender": None,
        "metrics": (),
        "geography": None,
        "channel": None,
        "period": None,
        "ambiguities": ambiguities,
    }

    categories = [canonical for canonical, terms in CATEGORY_TERMS if any(_contains(text, term) for term in terms)]
    if len(categories) == 1:
        result["category"] = categories[0]
    elif len(categories) > 1:
        ambiguities.append("multiple_categories")

    genders = [canonical for canonical, terms in GENDER_TERMS if any(_contains(text, term) for term in terms)]
    if len(genders) == 1:
        result["gender"] = genders[0]
    elif len(genders) > 1:
        ambiguities.append("multiple_genders")

    metrics = [canonical for canonical, terms in METRIC_TERMS if any(_contains(text, term) for term in terms)]
    if len(metrics) == 1:
        result["metrics"] = (metrics[0],)
    elif len(metrics) > 1:
        ambiguities.append("multiple_metrics")

    geographies = []
    if re.search(r"대한민국|한국|국내|\bkr\b|korea", text):
        geographies.append("KR")
    if re.search(r"일본|\bjp\b|japan", text):
        geographies.append("JP")
    if len(set(geographies)) == 1:
        result["geography"] = geographies[0]
    elif len(set(geographies)) > 1:
        ambiguities.append("multiple_geographies")

    channels = []
    if re.search(r"온라인|online|e-?commerce|이커머스", text):
        channels.append("online")
    if re.search(r"오프라인|offline|매장|점포", text):
        channels.append("offline")
    if len(set(channels)) == 1:
        result["channel"] = channels[0]
    elif len(set(channels)) > 1:
        ambiguities.append("multiple_channels")

    if re.search(r"분기|quarter|q[1-4]", text):
        ambiguities.append("unsupported_period_grain")
    else:
        month = re.search(r"(?<!\d)(20\d{2})\s*년?\s*(0?[1-9]|1[0-2])\s*월", text)
        iso_month = re.search(r"(?<!\d)(20\d{2})-(0[1-9]|1[0-2])(?!\d)", text)
        full_date = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", text)
        years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)
        if full_date:
            result["period"] = full_date.group(1)
        elif month or iso_month:
            match = month or iso_month
            result["period"] = f"{match.group(1)}-{int(match.group(2)):02d}"
        elif len(years) == 1:
            result["period"] = years[0]
        elif len(years) == 2:
            result["period"] = f"{years[0]}-{years[1]}"
        elif len(years) > 2:
            ambiguities.append("multiple_periods")

    if re.search(r"합성|synthetic|fixture|테스트|test\b|demo\b", text):
        result["intent"] = "synthetic_test"
    elif re.search(r"매핑|분류 기준|taxonomy|mapping", text):
        result["intent"] = "mapping_lookup"
    elif re.search(r"무슨 뜻|의미|정의|definition|what does", text):
        result["intent"] = "definition_lookup"
    elif len(metrics) > 1:
        # Do not let tuple ordering turn a mixed sales/market request into a
        # purchase-row lookup. Keep the intent unresolved until the caller
        # supplies one unambiguous metric.
        result["intent"] = "unresolved"
    elif metrics and metrics[0] == "purchase_rows":
        result["intent"] = "sales_observation"
    elif metrics:
        result["intent"] = "unsupported"
    elif re.search(r"판매|구매|purchase|sales", text):
        result["intent"] = "sales_observation"
    return result


def scope_conflicts(inferred: dict[str, Any], explicit: dict[str, Any]) -> tuple[str, ...]:
    """Describe disagreements while leaving explicit request fields authoritative."""
    conflicts = []
    for field in ("category", "gender", "geography", "channel", "period"):
        inferred_value = inferred.get(field)
        explicit_value = explicit.get(field)
        if inferred_value is not None and explicit_value not in (None, ""):
            if str(inferred_value).casefold() != str(explicit_value).casefold():
                conflicts.append(f"explicit_{field}_overrides_query_hint")
    metrics = inferred.get("metrics", ())
    explicit_metrics = explicit.get("metrics") or ()
    if metrics and explicit_metrics and not set(metrics).issubset({str(item) for item in explicit_metrics}):
        conflicts.append("explicit_metrics_override_query_hint")
    return tuple(conflicts)


def unresolved_ambiguities(inferred: dict[str, Any], explicit: dict[str, Any]) -> tuple[str, ...]:
    """Drop ambiguity only when an explicit field supplies the missing choice."""
    remaining = list(inferred.get("ambiguities", ()))
    resolutions = {
        "multiple_categories": "category", "multiple_genders": "gender",
        "multiple_geographies": "geography", "multiple_channels": "channel",
        "multiple_periods": "period", "unsupported_period_grain": "period",
        "multiple_metrics": "metrics",
    }
    for ambiguity, field in resolutions.items():
        value = explicit.get(field)
        if value not in (None, "", (), []):
            remaining = [item for item in remaining if item != ambiguity]
    return tuple(remaining)
