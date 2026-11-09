"""Map versioned local H&M aggregates into Project_07 sales-domain records.

Category mapping is supplied by the caller because Kaggle's H&M taxonomy is
not the project's agreed merchandising taxonomy. No season is inferred.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Mapping

from ...domain.models import EvidenceRecord, MarketResearchContext, PermissionStatus, ResultStatus, SourceReport
from ...domain.sales import SalesDataResult, SalesObservation

DEFAULT_PROCESSED_DIR = Path("data/processed/hm-personalized-fashion-recommendations")


class HmHistoricalSalesSource:
    """Optional historical H&M/Kaggle source; permission is unconfirmed by default."""

    def __init__(self, processed_dir: Path, category_map: Mapping[str, str], mapping_version: str,
                 *, usage_authorized: bool = False) -> None:
        self.processed_dir = processed_dir
        self.category_map = dict(category_map)
        self.mapping_version = mapping_version
        self.usage_authorized = usage_authorized

    def research(self, context: MarketResearchContext) -> SourceReport:
        category = context.requested_category
        if not category:
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=("H&M historical sales were not queried: requested_category is required.",),
            )
        try:
            sales = load_hm_sales_analysis(
                request_id=context.request_id,
                schema_version="1.1-proposal",
                requested_category=category,
                source_category_map=self.category_map,
                category_mapping_version=self.mapping_version,
                processed_dir=self.processed_dir,
            )
            manifest = json.loads((self.processed_dir.expanduser().resolve() / "manifest.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(f"H&M historical data is not available at {self.processed_dir}; provide processed manifest.json, article_catalog.csv, and weekly_sales_by_article_channel.csv.",),
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=("H&M historical data could not be read or its category mapping is invalid.",),
            )

        permitted = self.usage_authorized
        permission = PermissionStatus.AUTHORIZED if permitted else PermissionStatus.UNKNOWN
        period = manifest.get("observation_period", {})
        evidence: list[EvidenceRecord] = []
        observations = []
        if not permitted:
            sales = SalesDataResult(
                status="no_results",
                limitations=(*sales.limitations, "Kaggle data usage authorization is not confirmed; observations withheld until explicitly authorized."),
            )
        else:
            for item in sales.observations:
                for evidence_id, source_name, source_type in (
                    (item.evidence_ids[0], "H&M Kaggle transactions_train historical purchase rows", "historical_sales"),
                    (item.evidence_ids[1], "H&M Kaggle articles product taxonomy", "product_catalog"),
                ):
                    evidence.append(EvidenceRecord(
                        evidence_id=evidence_id,
                        source_id="hm-kaggle-personalized-fashion-recommendations",
                        source_type=source_type,
                        source_name=source_name,
                        locator="https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/data",
                        period_start=period.get("start"), period_end=period.get("end"),
                        geography=None, channel=item.channel,
                        product_mapping=self.mapping_version,
                        unit=item.unit,
                        metric_definition="Historical purchase transaction rows; not current sales, market demand, customer preference, or forecast.",
                        manifest_id=manifest.get("manifest_id"),
                        permission_status=permission,
                    ))
                observations.append(item)
        limitations = (*sales.limitations, "Historical Kaggle data is not live/current H&M sales. Its observed period is retained from the local manifest.")
        return SourceReport(
            status=ResultStatus.OK if permitted and observations else ResultStatus.NO_RESULTS,
            evidence=tuple(evidence),
            limitations=limitations,
            sales_data=sales,
        )


def load_hm_sales_analysis(
    *,
    request_id: str,
    schema_version: str,
    requested_category: str,
    source_category_map: Mapping[str, str],
    category_mapping_version: str,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    observation_limit: int | None = None,
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

    normalized_map = {key.strip().casefold(): value.strip() for key, value in source_category_map.items()}
    aggregates: dict[tuple[str, str, str], dict[str, object]] = {}
    unmatched_rows = 0
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
            mapped_category = (
                normalized_map.get(type_name.casefold())
                or normalized_map.get(group_name.casefold())
            )
            if mapped_category is None or mapped_category.casefold() != requested_category.strip().casefold():
                continue
            try:
                purchase_rows = int(row["purchase_rows"])
            except (ValueError, TypeError):
                continue
            if purchase_rows < 0:
                continue
            key = (mapped_category, group_name, type_name)
            metric = aggregates.setdefault(key, {"purchase_rows": 0, "articles": set(), "channels": set()})
            metric["purchase_rows"] = int(metric["purchase_rows"]) + purchase_rows
            metric["articles"].add(article_id)  # type: ignore[union-attr]
            metric["channels"].add((row.get("sales_channel_id") or "").strip())  # type: ignore[union-attr]

    observation = manifest.get("observation_period", {})
    period_start = observation.get("start")
    period_end = observation.get("end")
    if not period_start or not period_end:
        raise ValueError("manifest.json does not contain a valid observation period.")
    sales_fingerprint = str(manifest.get("source_transactions", {}).get("container_sha256", "unknown"))[:12]
    catalog_fingerprint = str(manifest.get("source_articles", {}).get("container_sha256", "unknown"))[:12]
    observations: list[SalesObservation] = []
    for (mapped, group_name, type_name), metric in sorted(aggregates.items()):
        category_evidence_id = f"E-HM-CATEGORY-{sales_fingerprint}-{catalog_fingerprint}-{group_name}-{type_name}-{period_start}-{period_end}"
        catalog_evidence_id = f"E-HM-TAXONOMY-{catalog_fingerprint}-{group_name}-{type_name}"
        channels = metric["channels"]
        observations.append(SalesObservation(
            observation_id=category_evidence_id,
            metric_name="purchase_rows",
            value=int(metric["purchase_rows"]),
            unit="purchase_rows",
            evidence_ids=(category_evidence_id, catalog_evidence_id),
            category=mapped,
            period_start=period_start,
            period_end=period_end,
            channel=next(iter(channels)) if len(channels) == 1 else None,
            metric_definition="Count of purchase transaction rows in the declared source period; not a customer count or demand estimate.",
            product_mapping=category_mapping_version,
        ))

    truncated = observation_limit is not None and len(observations) > observation_limit
    if truncated:
        observations = observations[:observation_limit]

    status = "ok" if observations else "no_results"
    limitations = [
        "Historical Kaggle purchase rows are observations, not future demand or customer-preference proof.",
        "The source has no explicit merchandising season; no season was inferred.",
        "Category mapping was supplied by the caller and must be reviewed against the project taxonomy.",
        f"Category mapping version: {category_mapping_version}.",
        "Source channel IDs are not mapped to online/store labels in this adapter.",
        "Price is omitted from concept context because its currency and commercial meaning are not established.",
        "Category sales were summed from weekly article aggregates joined to article_catalog by article_id; multi-type product_codes retain their article-level assignments.",
    ]
    if truncated:
        limitations.append(f"Category observations were limited to {observation_limit} mapped group/type rows; the returned rows do not represent the full mapped category total.")
    if unmatched_rows:
        limitations.append(f"{unmatched_rows} weekly article aggregate rows could not be joined to article_catalog and were excluded from category mapping.")
    if not observations:
        limitations.append("No processed article-level category aggregates matched the requested category and supplied mapping.")

    return SalesDataResult(
        status=status,
        observations=tuple(observations),
        limitations=tuple(limitations),
    )
