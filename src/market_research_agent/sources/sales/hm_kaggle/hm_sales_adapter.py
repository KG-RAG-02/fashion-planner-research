"""Map versioned local H&M aggregates into Project_07 sales-domain records.

Category mapping is supplied by the caller because Kaggle's H&M taxonomy is
not the project's agreed merchandising taxonomy. No season is inferred.
"""

from __future__ import annotations

import csv
import json
import re
import calendar
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

from ....domain.models import EvidenceRecord, MarketResearchContext, PermissionStatus, ResultStatus, SourceReport
from ....domain.sales import MissingData, SalesDataResult, SalesObservation

DEFAULT_PROCESSED_DIR = Path("data/processed/hm-personalized-fashion-recommendations")


class HmSourceReadError(ValueError):
    """A safe H&M source read or validation error."""


class HmHistoricalSalesSource:
    """Historical H&M/Kaggle source enabled by the Project_07 data owner."""

    def __init__(self, processed_dir: Path, category_map: Mapping[str, str], mapping_version: str,
                 *, usage_authorized: bool = False) -> None:
        self.processed_dir = processed_dir
        self.category_map = _validated_category_map(category_map)
        self.mapping_version = mapping_version
        # Retained as a compatibility argument; source use is enabled by the
        # Project_07 owner and is not gated per invocation.
        self.usage_authorized = True

    def research(self, context: MarketResearchContext) -> SourceReport:
        if context.query_intent in {"synthetic_test", "mapping_lookup", "definition_lookup", "unsupported"}:
            message = f"The H&M structured sales adapter does not serve {context.query_intent} requests."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message,),
                sales_data=SalesDataResult("no_results", limitations=(message,), missing_data=(
                    MissingData("sales_data", "not_available_from_source", message, "hm-kaggle-personalized-fashion-recommendations"),
                )),
            )
        if (
            "unsupported_period_grain" in context.query_ambiguities
            or (context.requested_period and _period_bounds(context.requested_period) is None)
        ):
            message = "H&M historical search supports year, month, or date ranges; the requested period grain is ambiguous or unsupported."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message,),
                sales_data=SalesDataResult("no_results", limitations=(message,), missing_data=(
                    MissingData("sales_data", "not_available_from_source", message, "hm-kaggle-personalized-fashion-recommendations"),
                )),
            )
        category = context.requested_category
        if not category:
            message = "H&M historical search requires one explicitly selected or unambiguously inferred category mapping."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message,),
                sales_data=SalesDataResult("no_results", limitations=(message,), missing_data=(
                    MissingData("sales_data", "mapping_failed", message, "hm-kaggle-personalized-fashion-recommendations"),
                )),
            )
        requested_metrics = {item.casefold().replace(" ", "_") for item in context.requested_metrics}
        if requested_metrics and "purchase_rows" not in requested_metrics:
            message = "H&M historical source only provides purchase_rows; requested metric is not available from this source."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message, "H&M 과거 데이터이며 매출·시장 규모·수요를 나타내지 않습니다."),
                sales_data=SalesDataResult("no_results", limitations=(message,), missing_data=(
                    MissingData("sales_data", "not_available_from_source", message, "hm-kaggle-personalized-fashion-recommendations"),
                )),
            )
        requested_gender = (context.explicit_scope or {}).get("gender") or (context.inferred_scope or {}).get("gender")
        if context.requested_geography or context.requested_channel or requested_gender:
            unsupported_dimensions = [
                name for name, requested in (
                    ("geography", context.requested_geography),
                    ("channel", context.requested_channel),
                    ("gender", requested_gender),
                ) if requested
            ]
            message = "H&M historical source does not provide verified mapping for: " + ", ".join(unsupported_dimensions) + "."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message, *(
                    ("H&M 과거 데이터의 원천 channel code는 온라인/오프라인으로 해석하지 않습니다.",)
                    if context.requested_channel else ()
                )),
                sales_data=SalesDataResult("no_results", limitations=(message,), missing_data=(
                    MissingData("sales_data", "not_available_from_source", message, "hm-kaggle-personalized-fashion-recommendations"),
                )),
            )
        if not self.mapping_version.strip():
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=("H&M historical sales require a category mapping version.",),
                sales_data=SalesDataResult(
                    "error",
                    errors=("Category mapping version is missing.",),
                    missing_data=(MissingData("sales_data", "mapping_failed", "Category mapping version is missing.", "hm-kaggle-personalized-fashion-recommendations"),),
                ),
            )
        if not self.category_map:
            message = "No H&M-to-request category mapping was configured."
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=(message,),
                sales_data=SalesDataResult(
                    "error", errors=(message,),
                    missing_data=(MissingData("sales_data", "mapping_failed", message, "hm-kaggle-personalized-fashion-recommendations"),),
                ),
            )
        if not any(target.casefold() == category.strip().casefold() for target in self.category_map.values()):
            message = "No H&M source category mapping targets the requested category."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message,),
                sales_data=SalesDataResult(
                    "no_results", limitations=(message,),
                    missing_data=(MissingData("sales_data", "mapping_failed", message, "hm-kaggle-personalized-fashion-recommendations"),),
                ),
            )
        try:
            sales, manifest = self._load_sales_analysis(context, category)
        except HmSourceReadError as exc:
            message = str(exc)
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=(message,),
                sales_data=SalesDataResult(
                    "error", errors=(message,),
                    missing_data=(MissingData("sales_data", "source_error", message, "hm-kaggle-personalized-fashion-recommendations", retryable=True),),
                ),
            )
        except FileNotFoundError:
            message = "H&M historical source files are not available; provide the processed manifest.json, article_catalog.csv, and weekly_sales_by_article_channel.csv."
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(message,),
                sales_data=SalesDataResult(
                    "no_results", limitations=(message,),
                    missing_data=(MissingData("sales_data", "source_not_configured", message, "hm-kaggle-personalized-fashion-recommendations"),),
                ),
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=("H&M historical data could not be read or its category mapping is invalid.",),
            )

        permission = PermissionStatus.AUTHORIZED
        evidence: list[EvidenceRecord] = []
        seen_evidence_ids: set[str] = set()
        observations = []
        for item in sales.observations:
            for evidence_id, source_name, source_type in (
                (item.evidence_ids[0], "H&M Kaggle transactions_train historical purchase rows", "historical_sales"),
                (item.evidence_ids[1], "H&M Kaggle articles product taxonomy", "product_catalog"),
            ):
                if evidence_id in seen_evidence_ids:
                    continue
                seen_evidence_ids.add(evidence_id)
                evidence.append(EvidenceRecord(
                    evidence_id=evidence_id,
                    source_id="hm-kaggle-personalized-fashion-recommendations",
                    source_type=source_type,
                    source_name=source_name,
                    locator=self._evidence_locator(source_type),
                    period_start=item.period_start if source_type == "historical_sales" else None,
                    period_end=item.period_end if source_type == "historical_sales" else None,
                    geography=None,
                    channel=item.channel if source_type == "historical_sales" else None,
                    product_mapping=self.mapping_version,
                    unit=item.unit,
                    metric_definition=item.metric_definition,
                    manifest_id=manifest.get("manifest_id"),
                    permission_status=permission,
                ))
            observations.append(item)
        limitations = (*sales.limitations, "H&M 과거 데이터이며 현재 H&M 판매나 시장 수요를 뜻하지 않습니다. 관측 기간은 source manifest에서 보존합니다.")
        unsupported_metrics = requested_metrics - {"purchase_rows"}
        if unsupported_metrics:
            limitations = (*limitations, "H&M returned purchase_rows only; other requested metrics are unavailable from this source.")
        return SourceReport(
            status=ResultStatus.PARTIAL if sales.status == "partial" or unsupported_metrics else ResultStatus.OK if observations else ResultStatus.NO_RESULTS,
            evidence=tuple(evidence),
            limitations=limitations,
            sales_data=sales,
        )

    @contextmanager
    def _open_processed_directory(self):
        """Yield the local processed source after request-scope validation."""
        yield self.processed_dir

    def _load_sales_analysis(self, context: MarketResearchContext, category: str):
        with self._open_processed_directory() as processed_dir:
            sales = load_hm_sales_analysis(
                request_id=context.request_id,
                schema_version="1.1-proposal",
                requested_category=category,
                source_category_map=self.category_map,
                category_mapping_version=self.mapping_version,
                processed_dir=processed_dir,
                requested_period=context.requested_period,
            )
            manifest = json.loads((processed_dir.expanduser().resolve() / "manifest.json").read_text(encoding="utf-8"))
        return sales, manifest

    def _evidence_locator(self, source_type: str) -> str:
        return "https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/data"


def load_hm_sales_analysis(
    *,
    request_id: str,
    schema_version: str,
    requested_category: str,
    source_category_map: Mapping[str, str],
    category_mapping_version: str,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    observation_limit: int | None = None,
    requested_period: str | None = None,
) -> SalesDataResult:
    """Load exact article-classified aggregates as a local historical example.

    The mapping may name either H&M ``product_group_name`` or
    ``product_type_name`` and map it to the request taxonomy. Sales are summed
    from weekly article rows after joining the catalog by article_id. This
    avoids assigning every article in a multi-type product_code to one
    representative type. The function does not infer season or channel labels.
    Kaggle/H&M data is not an authorized operating source by default.
    """

    if not request_id.strip() or not schema_version.strip():
        raise ValueError("request_id and schema_version are required.")
    if not requested_category.strip():
        raise ValueError("requested_category is required for category-scoped sales evidence.")
    if not category_mapping_version.strip():
        raise ValueError("category_mapping_version is required for reproducible sales mapping.")
    if observation_limit is not None and observation_limit < 1:
        raise ValueError("observation_limit must be at least 1 when supplied.")

    root = processed_dir.expanduser().resolve()
    manifest_path = root / "manifest.json"
    catalog_path = root / "article_catalog.csv"
    weekly_path = root / "weekly_sales_by_article_channel.csv"
    for path in (manifest_path, catalog_path, weekly_path):
        if not path.is_file():
            raise FileNotFoundError(f"Processed H&M input is missing: {path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog: dict[str, dict[str, str]] = {}
    with catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required_catalog = {"article_id", "product_code", "product_group_name", "product_type_name", "colour_group_name", "department_name"}
        missing = required_catalog - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"article_catalog.csv is missing columns: {sorted(missing)}")
        for row in reader:
            article_id = (row.get("article_id") or "").strip()
            if article_id:
                catalog[article_id] = row

    normalized_map = _validated_category_map(source_category_map)
    observation = manifest.get("observation_period", {})
    period_start = observation.get("start")
    period_end = observation.get("end")
    if not period_start or not period_end:
        raise ValueError("manifest.json does not contain a valid observation period.")
    manifest_start = date.fromisoformat(period_start)
    manifest_end = date.fromisoformat(period_end)
    if manifest_start > manifest_end:
        raise ValueError("manifest.json observation period is reversed.")
    period_bounds = _period_bounds(requested_period)
    # Preserve each source week as an observation. Grouping across time would
    # hide seasonality and make period comparisons impossible to reproduce.
    aggregates: dict[tuple[str, str, str, str, str], int] = {}
    unmatched_rows = 0
    invalid_rows = 0
    ambiguous_mapping_rows = 0
    with weekly_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"article_id", "purchase_rows", "week_start", "sales_channel_id"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"weekly_sales_by_article_channel.csv is missing columns: {sorted(missing)}")

        for row in reader:
            article_id = (row.get("article_id") or "").strip()
            meta = catalog.get(article_id)
            if meta is None:
                unmatched_rows += 1
                continue
            group_name = (meta.get("product_group_name") or "").strip() or "(unknown)"
            type_name = (meta.get("product_type_name") or "").strip() or "(unknown)"
            type_mapping = normalized_map.get(type_name.casefold())
            group_mapping = normalized_map.get(group_name.casefold())
            mapped_category = type_mapping or group_mapping
            if mapped_category is None:
                continue
            if mapped_category.casefold() != requested_category.strip().casefold():
                continue
            try:
                purchase_rows = int(row["purchase_rows"])
            except (ValueError, TypeError):
                invalid_rows += 1
                continue
            if purchase_rows < 0:
                invalid_rows += 1
                continue
            week_start = (row.get("week_start") or "").strip()
            if not week_start:
                invalid_rows += 1
                continue
            try:
                parsed_week_start = date.fromisoformat(week_start)
            except ValueError:
                invalid_rows += 1
                continue
            if parsed_week_start > manifest_end or parsed_week_start + timedelta(days=6) < manifest_start:
                invalid_rows += 1
                continue
            if period_bounds is not None and (
                parsed_week_start + timedelta(days=6) < period_bounds[0]
                or parsed_week_start > period_bounds[1]
            ):
                continue
            channel_code = (row.get("sales_channel_id") or "").strip() or None
            key = (mapped_category, group_name, type_name, week_start, channel_code or "(unknown)")
            aggregates[key] = aggregates.get(key, 0) + purchase_rows

    sales_fingerprint = str(manifest.get("source_transactions", {}).get("container_sha256", "unknown"))[:12]
    catalog_fingerprint = str(manifest.get("source_articles", {}).get("container_sha256", "unknown"))[:12]
    observations: list[SalesObservation] = []
    for (mapped, group_name, type_name, week_start, channel_code), purchase_rows in sorted(aggregates.items()):
        category_evidence_id = f"E-HM-CATEGORY-{sales_fingerprint}-{catalog_fingerprint}-{group_name}-{type_name}-{week_start}-{channel_code}"
        catalog_evidence_id = f"E-HM-TAXONOMY-{catalog_fingerprint}-{group_name}-{type_name}"
        start = max(date.fromisoformat(week_start), manifest_start)
        end = min(date.fromisoformat(week_start) + timedelta(days=6), manifest_end)
        observations.append(SalesObservation(
            observation_id=category_evidence_id,
            metric_name="purchase_rows",
            value=purchase_rows,
            unit="purchase_rows",
            evidence_ids=(category_evidence_id, catalog_evidence_id),
            category=mapped,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            period_grain="week",
            channel=None if channel_code == "(unknown)" else channel_code,
            metric_definition="Count of purchase transaction rows in the declared source period; not a customer count or demand estimate.",
            product_mapping=category_mapping_version,
        ))

    truncated = observation_limit is not None and len(observations) > observation_limit
    if truncated:
        observations = observations[:observation_limit]

    has_exclusions = (truncated if observation_limit is not None else False) or unmatched_rows > 0 or invalid_rows > 0 or ambiguous_mapping_rows > 0
    status = "partial" if observations and has_exclusions else "ok" if observations else "no_results"
    limitations = [
        "Historical Kaggle purchase rows are observations, not future demand or customer-preference proof.",
        "The source has no explicit merchandising season; no season was inferred.",
        f"Category mapping version: {category_mapping_version}.",
        "Exact product_type_name mappings take precedence over product_group_name fallback mappings.",
        "Source channel IDs are not mapped to online/store labels in this adapter.",
        "Source price is omitted because its currency and commercial meaning are not established.",
        "Category sales were summed from weekly article aggregates joined to article_catalog by article_id; multi-type product_codes retain their article-level assignments.",
    ]
    if truncated:
        limitations.append(f"Category observations were limited to {observation_limit} mapped group/type rows; the returned rows do not represent the full mapped category total.")
    if unmatched_rows:
        limitations.append(f"{unmatched_rows} weekly article aggregate rows could not be joined to article_catalog and were excluded from category mapping.")
    if invalid_rows:
        limitations.append(f"{invalid_rows} matching weekly aggregate rows had invalid dates or purchase_rows values and were excluded.")
    if ambiguous_mapping_rows:
        limitations.append(f"{ambiguous_mapping_rows} rows had conflicting product-type and product-group mappings and were excluded.")
    if not observations:
        limitations.append("No processed article-level category aggregates matched the requested category and supplied mapping.")

    return SalesDataResult(
        status=status,
        observations=tuple(observations),
        limitations=tuple(limitations),
        missing_data=tuple(item for item in (
            MissingData("sales_data", "mapping_failed", f"{unmatched_rows} sales rows could not be joined to the product catalog.", "hm-kaggle-personalized-fashion-recommendations") if unmatched_rows else None,
            MissingData("sales_data", "mapping_failed", f"{ambiguous_mapping_rows} rows had conflicting category mapping results.", "hm-kaggle-personalized-fashion-recommendations") if ambiguous_mapping_rows else None,
        ) if item is not None),
    )


def _validated_category_map(source_category_map: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for source, target in source_category_map.items():
        if not isinstance(source, str) or not source.strip() or not isinstance(target, str) or not target.strip():
            raise ValueError("Category mappings require non-empty source and target names.")
        key = source.strip().casefold()
        value = target.strip()
        prior = normalized.get(key)
        if prior is not None and prior.casefold() != value.casefold():
            raise ValueError("Category mapping contains conflicting case-insensitive source labels.")
        normalized[key] = value
    return normalized


def _period_bounds(value: str | None) -> tuple[date, date] | None:
    """Parse the deliberately small period syntax supported by H&M search."""
    if not value:
        return None
    text = value.strip()
    match = re.fullmatch(r"(20\d{2}-\d{2}-\d{2})\.\.(20\d{2}-\d{2}-\d{2})", text)
    if match:
        try:
            start = date.fromisoformat(match.group(1))
            end = date.fromisoformat(match.group(2))
        except ValueError:
            return None
        return (start, end) if start <= end else None
    match = re.fullmatch(r"(20\d{2})\s*[-–~]\s*(20\d{2})", text)
    if match:
        return date(int(match.group(1)), 1, 1), date(int(match.group(2)), 12, 31)
    match = re.fullmatch(r"(20\d{2})-(\d{2})", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        return None
    if re.fullmatch(r"20\d{2}", text):
        year = int(text)
        return date(year, 1, 1), date(year, 12, 31)
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return None
        return day, day
    return None
