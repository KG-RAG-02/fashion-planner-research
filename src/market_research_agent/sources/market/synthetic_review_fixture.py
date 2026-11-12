"""Offline, deterministic synthetic source for the Review stub profile.

Selected by ``VOGUE_P07_REVIEW_SOURCE=synthetic_local_fixture``. It reads only
the packaged bundle built by scripts/build_synthetic_review_fixture.py (a
verbatim excerpt of the seed-407 query coverage fixture). It never opens a
network connection, never filters by the reviewed Draft, and every value stays
labelled synthetic and test-only.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ...domain.models import EvidenceRecord, MarketResearchContext, PermissionStatus, ResultStatus, SourceReport
from ...domain.sales import MarketDataResult, MarketObservation, SalesDataResult, SalesObservation
from ...resource_paths import resource_root

SOURCE_MODE = "synthetic_local_fixture"
BUNDLE_SCHEMA_VERSION = "project07-synthetic-review-fixture/1.0"
MARKERS = {
    "synthetic": True,
    "data_nature": "synthetic_test",
    "permission_status": "authorized_for_synthetic_testing_only",
    "operational_eligible": False,
}
LIMITATION = (
    "SYNTHETIC TEST DATA ONLY (VOGUE_P07_REVIEW_SOURCE=synthetic_local_fixture): bundled values generated "
    "from the seed-407 query coverage fixture; not VOGUE or H&M sales, not observed market data, not a forecast."
)
SCOPE_LIMITATION = (
    "The same fixed synthetic excerpt (women knitwear, KR online, 2027-03 to 2027-05) is returned for every Draft; "
    "it is not filtered by, and is not evidence for, the reviewed Draft's category or season."
)
COMPARABILITY_LIMITATION = (
    "Sales-like item units and market-like KRW scenario values use different units and synthetic populations; "
    "they are not comparable."
)


def default_bundle_path() -> Path:
    return resource_root("synthetic_review_fixture") / "bundle.json"


def _check_markers(item: Any) -> None:
    if not isinstance(item, dict) or any(item.get(key) != value for key, value in MARKERS.items()):
        raise ValueError("Synthetic review fixture record lacks synthetic test-only markers.")


def load_bundle(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the packaged bundle; raise ValueError on any defect."""
    bundle = json.loads(Path(path or default_bundle_path()).read_text(encoding="utf-8"))
    _check_markers(bundle)
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION or bundle.get("source_mode") != SOURCE_MODE:
        raise ValueError("Unsupported synthetic review fixture version.")
    evidence = bundle.get("evidence")
    rows = [*bundle.get("sales_like", ()), *bundle.get("market_like", ())]
    if not isinstance(evidence, list) or not evidence or not rows:
        raise ValueError("Synthetic review fixture is empty.")
    for item in [*evidence, *rows]:
        _check_markers(item)
    evidence_ids = [item.get("evidence_id") for item in evidence]
    if len(set(evidence_ids)) != len(evidence_ids) or not all(isinstance(eid, str) and eid for eid in evidence_ids):
        raise ValueError("Synthetic review fixture evidence IDs are invalid.")
    for row in rows:
        value = row.get("value")
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
                or not row.get("evidence_ids") or not set(row["evidence_ids"]) <= set(evidence_ids)):
            raise ValueError("Synthetic review fixture row is invalid or unlinked.")
    return bundle


class SyntheticReviewFixtureSource:
    """Return the packaged synthetic excerpt; values stay labelled synthetic."""

    def __init__(self, bundle_path: Path | None = None) -> None:
        self.bundle_path = bundle_path

    def research(self, context: MarketResearchContext) -> SourceReport:
        try:
            bundle = load_bundle(self.bundle_path)
        except (OSError, ValueError, TypeError, KeyError):
            message = "Bundled synthetic review fixture is missing or invalid; no values were used."
            return SourceReport(
                status=ResultStatus.ERROR,
                limitations=(message, LIMITATION),
                sales_data=SalesDataResult("error", errors=(message,)),
                market_data=MarketDataResult("error", errors=(message,)),
            )
        evidence = tuple(self._evidence(item) for item in bundle["evidence"])
        sales = tuple(self._sales(row) for row in bundle["sales_like"])
        market = tuple(self._market(row) for row in bundle["market_like"])
        return SourceReport(
            status=ResultStatus.PARTIAL,
            evidence=evidence,
            limitations=(LIMITATION, SCOPE_LIMITATION, COMPARABILITY_LIMITATION),
            sales_data=SalesDataResult("partial", observations=sales, limitations=(LIMITATION, SCOPE_LIMITATION)),
            market_data=MarketDataResult("partial", observations=market, limitations=(LIMITATION, SCOPE_LIMITATION)),
        )

    @staticmethod
    def _evidence(item: dict[str, Any]) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=item["evidence_id"],
            source_id=item["source_id"],
            source_type=item["source_type"],
            source_name=item["source_name"],
            locator=f"synthetic://review-fixture/{item['source_id']}/{item['evidence_id']}",
            period_start=item.get("period_start"),
            period_end=item.get("period_end"),
            geography=item.get("geography"),
            channel=item.get("channel"),
            permission_status=PermissionStatus.SYNTHETIC_TEST_ONLY,
            synthetic=True,
            data_nature="synthetic_test",
            operational_eligible=False,
        )

    @staticmethod
    def _sales(row: dict[str, Any]) -> SalesObservation:
        return SalesObservation(
            observation_id=row["observation_id"],
            metric_name=row["metric_name"],
            value=row["value"],
            unit=row["unit"],
            evidence_ids=tuple(row["evidence_ids"]),
            category=row.get("category"),
            period_start=row.get("period_start"),
            period_end=row.get("period_end"),
            period_grain=row.get("period_grain"),
            geography=row.get("geography"),
            channel=row.get("channel"),
            metric_definition="Generated retailer-like item-unit scenario; not actual H&M, VOGUE, or observed company sales.",
            product_mapping=row.get("product_mapping_version"),
            measure_type=row.get("measure_type", "synthetic_sales_aggregate"),
            population=row.get("population"),
            gender=row.get("gender"),
            season=row.get("season"),
            season_year=row.get("season_year"),
            product_mapping_id=row.get("product_mapping_id"),
            product_mapping_version=row.get("product_mapping_version"),
            **MARKERS,
        )

    @staticmethod
    def _market(row: dict[str, Any]) -> MarketObservation:
        return MarketObservation(
            observation_id=row["observation_id"],
            metric_name=row["metric_name"],
            measure_type=row["measure_type"],
            value=row["value"],
            unit=row["unit"],
            evidence_ids=tuple(row["evidence_ids"]),
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
            **MARKERS,
        )
