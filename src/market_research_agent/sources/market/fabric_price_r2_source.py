"""Read and analyze the owner-provided public fabric listing snapshot in R2."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import os
import threading
import unicodedata
from statistics import median
from typing import Any

from ..shared.r2_object_store import R2ObjectError, R2ObjectStore


DEFAULT_FABRIC_PRICE_KEY = "example/fabric-prices/fabric_price_list.csv"
_MAX_BYTES = 1_000_000
_REQUIRED_COLUMNS = {
    "record_id", "product_name", "category", "composition", "construction_or_finish",
    "weight_g_per_cut", "width_cm", "color", "original_unit", "price_amount", "currency",
    "unit_price_krw_per_meter", "supplier", "shipping_terms", "source_url", "observed_at",
    "price_history_status", "source_page_type", "estimated_sales_unit",
    "estimated_length_m",
    "unit_price_estimation_status", "unit_price_estimation_method",
    "unit_price_estimation_confidence", "unit_price_estimation_evidence_urls",
}
_MATERIAL_ALIASES = {
    "linen": ("린넨",), "cotton": ("면",), "denim": ("데님",),
    "nylon": ("나일론",), "polyester": ("폴리",),
    "spandex": ("스판",), "elastane": ("스판",),
}
_CACHE: dict[tuple[str, str, str, str], tuple[dict[str, Any], ...]] = {}
_CACHE_LOCK = threading.Lock()


class FabricPriceSourceError(RuntimeError):
    """Safe error raised when the R2 fabric listing snapshot is unavailable."""


def _normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _positive_decimal(value: str) -> Decimal | None:
    try:
        amount = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _material_terms(material: str) -> tuple[str, ...]:
    query = _normalize(material)
    terms = [query] if query else []
    for alias, values in _MATERIAL_ALIASES.items():
        if alias in query:
            terms.extend(_normalize(value) for value in values)
    return tuple(dict.fromkeys(terms))


class R2FabricPriceSource:
    """Aggregate catalog listing prices; preserve unknown sales units as unknown."""

    def __init__(self, store: R2ObjectStore | None = None, *, object_key: str | None = None) -> None:
        self.store = store
        self.object_key = (object_key or os.getenv("PROJECT07_FABRIC_PRICE_R2_KEY", "").strip()
                           or DEFAULT_FABRIC_PRICE_KEY)

    def analyze(self, product: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        material = product.get("material") if isinstance(product, dict) else None
        if not isinstance(material, str) or not material.strip():
            return ()
        try:
            store = self.store or R2ObjectStore.from_env()
            head = store._get_client().head_object(Bucket=store.settings.bucket, Key=self.object_key)
            size = int(head.get("ContentLength", 0))
            version = str(head.get("VersionId") or head.get("ETag", "")).strip('"')
            if size <= 0 or size > _MAX_BYTES or not version:
                raise FabricPriceSourceError("The R2 fabric price object has invalid metadata or size.")
            cache_key = (store.settings.bucket, self.object_key, version, _normalize(material))
            with _CACHE_LOCK:
                if cache_key not in _CACHE:
                    content, metadata = store.get_bytes_and_metadata(self.object_key, max_bytes=_MAX_BYTES)
                    after = store._get_client().head_object(Bucket=store.settings.bucket, Key=self.object_key)
                    after_version = str(after.get("VersionId") or after.get("ETag", "")).strip('"')
                    if after_version != version:
                        raise FabricPriceSourceError("The R2 fabric price snapshot changed during the read.")
                    expected_hash = metadata.get("sha256")
                    actual_hash = hashlib.sha256(content).hexdigest()
                    if expected_hash and expected_hash.casefold() != actual_hash:
                        raise FabricPriceSourceError("The R2 fabric price object's SHA-256 metadata does not match.")
                    _CACHE[cache_key] = self._analyze_csv(
                        content, material.strip(), version=version,
                        sha256=actual_hash, object_key=self.object_key,
                    )
                return _CACHE[cache_key]
        except FabricPriceSourceError:
            raise
        except R2ObjectError:
            raise FabricPriceSourceError("The R2 fabric price snapshot could not be read safely.") from None
        except Exception:
            raise FabricPriceSourceError("The R2 fabric price snapshot could not be analyzed safely.") from None

    @staticmethod
    def _analyze_csv(content: bytes, material: str, *, version: str, sha256: str,
                     object_key: str) -> tuple[dict[str, Any], ...]:
        try:
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="strict")))
            if not reader.fieldnames or not _REQUIRED_COLUMNS.issubset(reader.fieldnames):
                raise FabricPriceSourceError("The R2 fabric price snapshot schema is unsupported.")
            rows = [dict(row) for row in reader]
        except (UnicodeDecodeError, csv.Error):
            raise FabricPriceSourceError("The R2 fabric price snapshot is not valid UTF-8 CSV.") from None
        if not rows:
            return ()

        terms = _material_terms(material)
        selected = []
        for row in rows:
            search_text = _normalize(" ".join((row.get("category", ""), row.get("composition", ""))))
            if not any(term and term in search_text for term in terms):
                continue
            amount = _positive_decimal(row.get("price_amount", ""))
            if amount is None or not row.get("record_id") or not row.get("source_url"):
                continue
            selected.append((row, amount))
        if not selected:
            return ()

        groups: dict[str, list[tuple[dict[str, str], Decimal]]] = {}
        for row, amount in selected:
            groups.setdefault(row["category"].strip(), []).append((row, amount))
        results = []
        for category, entries in sorted(groups.items()):
            amounts = [amount for _row, amount in entries]
            currencies = {row.get("currency", "").strip().upper() for row, _amount in entries}
            currency = next(iter(currencies)) if len(currencies) == 1 else None
            per_meter = [value for row, _amount in entries
                         if (value := _positive_decimal(row.get("unit_price_krw_per_meter", ""))) is not None]
            per_meter_statuses = {
                row.get("unit_price_estimation_status", "").strip()
                for row, _amount in entries
                if _positive_decimal(row.get("unit_price_krw_per_meter", "")) is not None
            }
            confidence_counts: dict[str, int] = {}
            for row, _amount in entries:
                if _positive_decimal(row.get("unit_price_krw_per_meter", "")) is not None:
                    confidence = row.get("unit_price_estimation_confidence", "unknown").strip() or "unknown"
                    confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
            comparables = []
            for row, amount in entries:
                comparables.append({
                    "record_id": row["record_id"].strip(),
                    "product_name": row["product_name"].strip(),
                    "composition": row["composition"].strip() or None,
                    "construction_or_finish": row["construction_or_finish"].strip() or None,
                    "weight_g_per_cut": row["weight_g_per_cut"].strip() or None,
                    "width_cm": row["width_cm"].strip() or None,
                    "color": row["color"].strip() or None,
                    "original_unit": row["original_unit"].strip() or None,
                    "estimated_sales_unit": row.get("estimated_sales_unit", "").strip() or None,
                    "estimated_length_m": row.get("estimated_length_m", "").strip() or None,
                    "listed_price": float(amount),
                    "currency": row.get("currency", "").strip().upper() or None,
                    "unit_price_krw_per_meter": float(value) if (value := _positive_decimal(row.get("unit_price_krw_per_meter", ""))) else None,
                    "unit_price_estimation_status": row.get("unit_price_estimation_status", "").strip() or None,
                    "unit_price_estimation_method": row.get("unit_price_estimation_method", "").strip() or None,
                    "unit_price_estimation_confidence": row.get("unit_price_estimation_confidence", "").strip() or None,
                    "unit_price_estimation_evidence_urls": row.get("unit_price_estimation_evidence_urls", "").strip() or None,
                    "supplier": row["supplier"].strip() or None,
                    "shipping_terms": row["shipping_terms"].strip() or None,
                    "source_url": row["source_url"].strip(),
                    "observed_at": row["observed_at"].strip() or None,
                    "source_page_type": row["source_page_type"].strip() or None,
                    "price_history_status": row["price_history_status"].strip() or None,
                })
            results.append({
                "material_query": material,
                "source_category": category,
                "source_type": "owner_provided_public_fabric_listing_snapshot",
                "source_grade": "public_sale",
                "sample_count": len(entries),
                "currency": currency,
                "original_unit": "판매 단위 미확인" if any(
                    not row.get("original_unit", "").strip()
                    or "미확인" in row.get("original_unit", "") for row, _amount in entries
                ) else sorted({row["original_unit"].strip() for row, _amount in entries}),
                "listed_price_statistics": {
                    "minimum": float(min(amounts)),
                    "median": float(median(amounts)),
                    "maximum": float(max(amounts)),
                    "currency": currency,
                    "statistic": "unweighted_median_of_source_listing_rows; original sales unit is unverified",
                },
                "price_per_meter_krw_statistics": ({
                    "sample_count": len(per_meter), "minimum": float(min(per_meter)),
                    "median": float(median(per_meter)), "maximum": float(max(per_meter)),
                    "currency": "KRW", "unit": "KRW/m",
                    "status": "estimated" if "estimated_not_verified" in per_meter_statuses else "observed",
                    "confidence_counts": confidence_counts,
                    "method": "estimated listed_price / 0.90m (1 Korean ma)",
                } if per_meter else None),
                "estimated_price_per_meter_available": bool(per_meter),
                "verified_price_per_meter_available": "observed" in per_meter_statuses,
                "cost_per_meter_operational_eligible": False,
                "category_mapping": {
                    "mapping_id": "project07-fabric-material-terms",
                    "mapping_version": "1.0",
                    "matching": "normalized material term substring against source category and composition",
                    "matched_source_categories": [category],
                    "matched_terms": list(terms),
                },
                "sources": comparables,
                "source_object": {
                    "object_key": object_key,
                    "version": version,
                    "sha256": sha256,
                },
                "permission_status": "owner_provided_snapshot; source reuse terms not independently verified",
                "operational_eligible": False,
                "limitations": [
                    "This is a point-in-time public listing snapshot, not a supplier quote or verified purchase cost.",
                    "Per-meter values are low-confidence estimates based on one listed-price unit being 1 Korean ma (90 cm); item-level units are not confirmed.",
                    "Estimated per-meter prices are research references and are not automatically included in product margin costing.",
                    "Material percentages and some textile specifications are missing or unverified.",
                    "Shipping terms are separate and must not be treated as included in the listed price.",
                    "The snapshot has no prior history, so price trends and changes cannot be calculated.",
                ],
            })
        return tuple(results)
