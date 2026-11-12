"""Input adapter for the unratified canonical orchestration request proposal.

This module deliberately accepts only a structured query and never accepts
Trend or Inventory result payloads. The stable HTTP handoff v0.1 remains a
separate compatibility boundary.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain.models import MarketResearchContext

CONTRACT_VERSION = "vogue-orchestration-request/0.1-proposal"
_TOP_LEVEL_FIELDS = {
    "contract_version", "request_id", "created_at", "locale", "timezone",
    "raw_query", "query_parser", "intent", "scope", "requested_agents",
}
_SCOPE_FIELDS = {
    "category", "gender", "season", "period", "geography", "channel",
    "metrics", "ambiguities",
}
_CERTAINTIES = {"explicit", "inferred", "defaulted"}
_PERIOD_CERTAINTIES = {"resolved", "unresolved"}
_ALLOWED_INTENTS = {
    "sales_observation", "market_observation", "mapping_lookup",
    "definition_lookup", "product_opportunity_research", "unsupported",
}


class CanonicalRequestError(ValueError):
    """A canonical request is malformed or violates the local proposal."""


def context_from_canonical_request(value: Any) -> MarketResearchContext:
    """Validate a canonical request and map its scope into Project_07 context.

    No defaults are invented, the query parser is not called, and trend/inventory
    payloads are rejected rather than silently accepted or forwarded.
    """
    if not isinstance(value, Mapping):
        raise CanonicalRequestError("request must be an object")
    extra = set(value) - _TOP_LEVEL_FIELDS
    if extra:
        # This also rejects trend_result / inventory_product_result, keeping
        # Project_07's new orchestration input independent of sibling outputs.
        raise CanonicalRequestError("request contains unsupported fields")
    if value.get("contract_version") != CONTRACT_VERSION:
        raise CanonicalRequestError("unsupported orchestration request contract_version")

    request_id = _required_text(value.get("request_id"), "request_id")
    if len(request_id) > 128 or any(not (char.isalnum() or char in "-_.:") for char in request_id):
        raise CanonicalRequestError("request_id must be 1 to 128 safe identifier characters")
    raw_query = _required_text(value.get("raw_query"), "raw_query")
    if len(raw_query) > 1000:
        raise CanonicalRequestError("raw_query exceeds the 1000 character limit")
    created_at = _aware_datetime(value.get("created_at"), "created_at")
    timezone = _required_text(value.get("timezone"), "timezone")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise CanonicalRequestError("timezone must be a recognized IANA timezone") from None
    _required_text(value.get("locale"), "locale")

    parser = value.get("query_parser")
    if not isinstance(parser, Mapping) or set(parser) != {"name", "version", "reference_time"}:
        raise CanonicalRequestError("query_parser must be an object")
    parser_name = _required_text(parser.get("name"), "query_parser.name")
    parser_version = _required_text(parser.get("version"), "query_parser.version")
    reference_time = _aware_datetime(parser.get("reference_time"), "query_parser.reference_time")
    if reference_time > created_at:
        raise CanonicalRequestError("query_parser.reference_time cannot be after created_at")

    intent = value.get("intent")
    if not isinstance(intent, Mapping) or set(intent) != {"primary", "requested_action", "confidence"}:
        raise CanonicalRequestError("intent must be an object")
    primary = _required_text(intent.get("primary"), "intent.primary")
    action = _required_text(intent.get("requested_action"), "intent.requested_action")
    if primary not in _ALLOWED_INTENTS:
        raise CanonicalRequestError("intent.primary is unsupported")
    confidence = intent.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise CanonicalRequestError("intent.confidence must be between 0 and 1")

    scope = value.get("scope")
    if not isinstance(scope, Mapping) or set(scope) != _SCOPE_FIELDS:
        raise CanonicalRequestError("scope must contain exactly the supported canonical fields")
    normalized: dict[str, Any] = {}
    flat_values: dict[str, Any] = {}
    for field in ("category", "gender", "season", "geography", "channel"):
        raw = scope.get(field)
        if raw is None:
            normalized[field] = None
            flat_values[field] = None
            continue
        normalized[field] = _scoped_value(raw, f"scope.{field}")
        flat_values[field] = normalized[field]["value"]

    period = scope.get("period")
    normalized["period"] = _period_value(period)
    metrics = scope.get("metrics")
    if not isinstance(metrics, list) or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in metrics):
        raise CanonicalRequestError("scope.metrics must be an array of non-empty strings")
    if len(set(metrics)) != len(metrics):
        raise CanonicalRequestError("scope.metrics must not contain duplicates")
    normalized["metrics"] = list(metrics)
    ambiguities = scope.get("ambiguities")
    if not isinstance(ambiguities, list) or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in ambiguities):
        raise CanonicalRequestError("scope.ambiguities must be an array of non-empty strings")
    normalized["ambiguities"] = list(ambiguities)
    if normalized["period"]["certainty"] == "unresolved" and "unresolved_period" not in normalized["ambiguities"]:
        normalized["ambiguities"].append("unresolved_period")

    requested_agents = value.get("requested_agents")
    if (
        not isinstance(requested_agents, list)
        or any(not isinstance(item, str) or not item.strip() for item in requested_agents)
        or len(set(requested_agents)) != len(requested_agents)
        or "sales_market" not in requested_agents
    ):
        raise CanonicalRequestError("requested_agents must uniquely include sales_market")

    period_text = None
    if normalized["period"]["certainty"] == "resolved":
        period_start = normalized["period"]["start"]
        period_end = normalized["period"]["end"]
        period_text = period_start if period_start == period_end else f"{period_start}..{period_end}"

    explicit_scope = {
        field: flat_values[field]
        for field in ("category", "gender", "season", "geography", "channel")
        if normalized[field] is not None and normalized[field]["certainty"] == "explicit"
    }
    inferred_scope = {
        field: normalized[field]
        for field in ("category", "gender", "season", "geography", "channel")
        if normalized[field] is not None and normalized[field]["certainty"] != "explicit"
    }
    if normalized["period"]["certainty"] == "resolved":
        period_scope = {
            "start": normalized["period"]["start"],
            "end": normalized["period"]["end"],
            "certainty": normalized["period"]["certainty"],
            "rule_version": normalized["period"]["rule_version"],
            "source": normalized["period"]["source"],
        }
        inferred_scope["period"] = period_scope
        if normalized["period"]["source"] == "raw_query":
            explicit_scope["period"] = period_text
    else:
        inferred_scope["period"] = normalized["period"]

    # Product recommendations, market sizing, and other non-purchase-row
    # requests must not accidentally activate the H&M purchase-row adapter.
    if primary == "sales_observation" and set(metrics).issubset({"purchase_rows"}) and "purchase_rows" in metrics:
        query_intent = "sales_observation"
    elif primary == "mapping_lookup":
        query_intent = "mapping_lookup"
    elif primary == "definition_lookup":
        query_intent = "definition_lookup"
    else:
        query_intent = "unsupported"

    planning_context: dict[str, Any] = {}
    if flat_values["category"] is not None:
        planning_context["category"] = flat_values["category"]
    if flat_values["gender"] is not None:
        planning_context["gender"] = flat_values["gender"]
    if flat_values["season"] is not None:
        planning_context["season"] = flat_values["season"]
    planning_request: dict[str, Any] = {
        "schema_version": "1.0",
        "request_id": request_id,
        "query": raw_query,
    }
    if planning_context:
        planning_request["context"] = planning_context

    return MarketResearchContext(
        request_id=request_id,
        planning_request=planning_request,
        requested_category=flat_values["category"],
        requested_metrics=tuple(metrics),
        requested_geography=flat_values["geography"],
        requested_channel=flat_values["channel"],
        requested_query=raw_query,
        requested_period=period_text,
        inferred_scope=inferred_scope,
        explicit_scope=explicit_scope,
        query_intent=query_intent,
        query_ambiguities=tuple(normalized["ambiguities"]),
        parser_version=f"{parser_name}/{parser_version}",
        canonical_request=deepcopy(dict(value)),
    )


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CanonicalRequestError(f"{field} must be a non-empty string")
    return value.strip()


def _aware_datetime(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise CanonicalRequestError(f"{field} must be an RFC3339 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanonicalRequestError(f"{field} must include a timezone offset")
    return parsed


def _scoped_value(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"value", "certainty", "source"}:
        raise CanonicalRequestError(f"{field} must contain value, certainty, and source")
    return {
        "value": _required_text(value.get("value"), f"{field}.value"),
        "certainty": _certainty(value.get("certainty"), f"{field}.certainty"),
        "source": _required_text(value.get("source"), f"{field}.source"),
    }


def _certainty(value: Any, field: str) -> str:
    if value not in _CERTAINTIES:
        raise CanonicalRequestError(f"{field} is unsupported")
    return str(value)


def _period_value(value: Any) -> dict[str, str | None]:
    fields = {"start", "end", "certainty", "rule_version", "source"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise CanonicalRequestError("scope.period must contain start, end, certainty, rule_version, and source")
    certainty = value.get("certainty")
    if certainty not in _PERIOD_CERTAINTIES:
        raise CanonicalRequestError("scope.period.certainty must be resolved or unresolved")
    certainty = str(certainty)
    rule_version = value.get("rule_version")
    source = value.get("source")
    if rule_version is not None:
        rule_version = _required_text(rule_version, "scope.period.rule_version")
    if source is not None:
        source = _required_text(source, "scope.period.source")
    start = value.get("start")
    end = value.get("end")
    if certainty == "resolved":
        start = _iso_date(start, "scope.period.start")
        end = _iso_date(end, "scope.period.end")
        if start > end:
            raise CanonicalRequestError("scope.period.start cannot be after scope.period.end")
        if rule_version is None or source is None:
            raise CanonicalRequestError("resolved period requires rule_version and source")
    elif start is not None or end is not None:
        raise CanonicalRequestError("unresolved period cannot carry resolved dates")
    return {
        "start": start,
        "end": end,
        "certainty": certainty,
        "rule_version": rule_version,
        "source": source,
    }


def _iso_date(value: Any, field: str) -> str:
    text = _required_text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise CanonicalRequestError(f"{field} must be an ISO date") from None
    if parsed.isoformat() != text:
        raise CanonicalRequestError(f"{field} must use YYYY-MM-DD format")
    return text
