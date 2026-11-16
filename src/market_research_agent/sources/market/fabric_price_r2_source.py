"""Read and analyze the owner-provided public fabric listing snapshot in R2."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import logging
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from statistics import median
from typing import Any

from ..shared.r2_object_store import (
    R2ConfigurationError, R2ObjectError, R2ObjectStore, _safe_failure_code,
)
from ..shared.r2_source_manifest import r2_source_key


DEFAULT_FABRIC_PRICE_KEY = "example/fabric-prices/fabric_price_list.csv"
_MAX_BYTES = 1_000_000
_REQUIRED_COLUMNS = {
    "record_id", "product_name", "category", "composition", "construction_or_finish",
    "weight_g_per_cut", "width_cm", "color", "original_unit", "price_amount", "currency",
    "supplier", "shipping_terms", "source_url", "observed_at",
    "price_history_status", "source_page_type", "estimated_sales_unit",
    "estimated_length_m",
    "unit_price_estimation_status", "unit_price_estimation_method",
    "unit_price_estimation_confidence", "unit_price_estimation_evidence_urls",
    "unit_price_krw_per_meter",
}
_MATERIAL_ALIASES = {
    "linen": ("린넨", "리넨"), "cotton": ("면",), "denim": ("데님",),
    "nylon": ("나일론",), "polyester": ("폴리",),
    "spandex": ("스판",), "elastane": ("스판",),
    "wool": ("울", "모직"), "cashmere": ("캐시미어",),
    "viscose": ("레이온", "인견"), "rayon": ("레이온", "인견"),
    "acrylic": ("아크릴",), "modal": ("모달",), "lyocell": ("텐셀", "리오셀"),
    "tencel": ("텐셀", "리오셀"), "silk": ("실크", "명주"),
    "leather": ("레더", "가죽"), "polyurethane": ("폴리우레탄", "우레탄"),
    "knit": ("니트", "편물", "knitted"),
}
_CACHE: OrderedDict[tuple[str, str, str, str], tuple[dict[str, Any], ...]] = OrderedDict()
_OBJECT_CACHE: OrderedDict[tuple[str, str, str], tuple[bytes, str]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)
_CACHE_MAX_ANALYSES = 256
_CACHE_MAX_OBJECTS = 4


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


_UNIT_LENGTH_PATTERN = re.compile(
    r"(?P<quantity>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>millimeters?|millimetres?|mm|"
    r"centimeters?|centimetres?|cm|meters?|metres?|m|yards?|yd|마)(?=$|[^A-Za-z])",
    re.IGNORECASE,
)


def _listing_length_m(row: dict[str, str]) -> tuple[Decimal | None, str | None]:
    """Return the listed cut length in meters with the basis kept explicit."""
    estimated_length = _positive_decimal(row.get("estimated_length_m", ""))
    if estimated_length is not None and estimated_length <= Decimal("1000"):
        return estimated_length, "source estimated_length_m"

    unit_text = " ".join((row.get("original_unit", ""), row.get("estimated_sales_unit", "")))
    match = _UNIT_LENGTH_PATTERN.search(unit_text)
    if not match:
        return None, None
    quantity = Decimal(match.group("quantity"))
    unit = match.group("unit").casefold()
    meters_per_unit = (
        Decimal("0.001") if unit in {"mm", "millimeter", "millimeters", "millimetre", "millimetres"} else
        Decimal("0.01") if unit in {"cm", "centimeter", "centimeters", "centimetre", "centimetres"} else
        Decimal("0.9144") if unit in {"yd", "yard", "yards"} else
        Decimal("0.9") if unit == "마" else
        Decimal("1")
    )
    return quantity * meters_per_unit, f"unit conversion from {match.group(0).strip()}"


def _per_meter_price(row: dict[str, str], listed_amount: Decimal) -> dict[str, Any] | None:
    """Preserve source unit prices; otherwise derive a labeled estimate when safe."""
    source_value = _positive_decimal(row.get("unit_price_krw_per_meter", ""))
    source_status = row.get("unit_price_estimation_status", "").strip().casefold()
    if source_value is not None:
        observed = source_status in {"observed", "verified"}
        return {
            "value": source_value,
            "status": "observed" if observed else "estimated_not_verified",
            "method": row.get("unit_price_estimation_method", "").strip() or (
                "source-observed per-meter value" if observed else
                "source-supplied per-meter value; verification not stated"
            ),
            "confidence": row.get("unit_price_estimation_confidence", "").strip() or (
                "high" if observed else "low"
            ),
        }
    if row.get("currency", "").strip().upper() != "KRW":
        return None
    length_m, basis = _listing_length_m(row)
    if length_m is None or length_m <= 0:
        return None
    return {
        "value": listed_amount / length_m,
        "status": "estimated_not_verified",
        "method": f"listed price / listed cut length ({basis}); estimate only, not a procurement cost",
        "confidence": "low",
    }


def _material_terms(material: str) -> tuple[str, ...]:
    query = _normalize(material)
    terms = [query] if query else []
    # Korean ``모`` means wool, but substring matching it would also match
    # unrelated words such as ``모달``. Treat it as a whole query alias only.
    if query == "모":
        terms.extend(("울", "wool", "모직"))
    for alias, values in _MATERIAL_ALIASES.items():
        if alias in query or any(_normalize(value) in query for value in values):
            terms.extend(_normalize(value) for value in values)
            terms.append(_normalize(alias))
    return tuple(dict.fromkeys(terms))


class R2FabricPriceSource:
    """Aggregate catalog listing prices; preserve unknown sales units as unknown."""

    def __init__(self, store: R2ObjectStore | None = None, *, object_key: str | None = None) -> None:
        self.store = store
        try:
            self.object_key = object_key or r2_source_key(
                "fabric_prices", default=DEFAULT_FABRIC_PRICE_KEY,
            )
        except Exception:
            raise FabricPriceSourceError(
                "The Project_07 R2 fabric price source manifest is invalid or unreadable."
            ) from None

    def analyze(self, product: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        analyze_started = time.perf_counter()
        material = product.get("material") if isinstance(product, dict) else None
        if not isinstance(material, str) or not material.strip():
            return ()
        try:
            if (not self.object_key or self.object_key.startswith("/")
                    or ".." in self.object_key.split("/")
                    or not all(character.isascii() and (character.isalnum() or character in "-._/")
                               for character in self.object_key)):
                raise FabricPriceSourceError("The Project_07 R2 fabric price object key is invalid.")
            if self.store is None:
                required_settings = (
                    "R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                )
                if not any(os.getenv(name, "").strip() for name in (*required_settings, "R2_ENDPOINT")):
                    # Source integration is optional. Do not report an operational
                    # failure when a local/stub environment intentionally has no R2.
                    return ()
                try:
                    store = R2ObjectStore.from_env()
                except R2ConfigurationError:
                    raise FabricPriceSourceError(
                        "R2 fabric price access is configured incompletely or unsafely."
                    ) from None
            else:
                store = self.store
            try:
                metadata_started = time.perf_counter()
                head = store._get_client().head_object(Bucket=store.settings.bucket, Key=self.object_key)
            except Exception as exc:
                raise FabricPriceSourceError(
                    f"R2 fabric price snapshot metadata could not be read ({_safe_failure_code(exc)})."
                ) from None
            size = int(head.get("ContentLength", 0))
            version = str(head.get("VersionId") or head.get("ETag", "")).strip('"')
            if size <= 0 or size > _MAX_BYTES or not version:
                raise FabricPriceSourceError("The R2 fabric price object has invalid metadata or size.")
            _LOGGER.info("Fabric price R2 metadata: declared_bytes=%d elapsed=%.3fs",
                         size, time.perf_counter() - metadata_started)
            cache_key = (store.settings.bucket, self.object_key, version, _normalize(material))
            with _CACHE_LOCK:
                if cache_key not in _CACHE:
                    object_cache_key = (store.settings.bucket, self.object_key, version)
                    cached_object = _OBJECT_CACHE.get(object_cache_key)
                    if cached_object is None:
                        read_started = time.perf_counter()
                        content, metadata = store.get_bytes_and_metadata(self.object_key, max_bytes=_MAX_BYTES)
                        after = store._get_client().head_object(Bucket=store.settings.bucket, Key=self.object_key)
                        after_version = str(after.get("VersionId") or after.get("ETag", "")).strip('"')
                        if after_version != version:
                            raise FabricPriceSourceError("The R2 fabric price snapshot changed during the read.")
                        expected_hash = metadata.get("sha256")
                        actual_hash = hashlib.sha256(content).hexdigest()
                        if expected_hash and expected_hash.casefold() != actual_hash:
                            raise FabricPriceSourceError("The R2 fabric price object's SHA-256 metadata does not match.")
                        _OBJECT_CACHE[object_cache_key] = (content, actual_hash)
                        _OBJECT_CACHE.move_to_end(object_cache_key)
                        while len(_OBJECT_CACHE) > _CACHE_MAX_OBJECTS:
                            _OBJECT_CACHE.popitem(last=False)
                        _LOGGER.info("Fabric price object read bytes=%d elapsed=%.3fs", len(content),
                                     time.perf_counter() - read_started)
                    else:
                        content, actual_hash = cached_object
                        _OBJECT_CACHE.move_to_end(object_cache_key)
                        _LOGGER.info("Fabric price object cache hit bytes=%d", len(content))
                    analysis_started = time.perf_counter()
                    _CACHE[cache_key] = self._analyze_csv(
                        content, material.strip(), version=version,
                        sha256=actual_hash, object_key=self.object_key,
                    )
                    _CACHE.move_to_end(cache_key)
                    while len(_CACHE) > _CACHE_MAX_ANALYSES:
                        _CACHE.popitem(last=False)
                    _LOGGER.info("Fabric price CSV analysis completed in %.3fs rows=%d",
                                 time.perf_counter() - analysis_started, len(_CACHE[cache_key]))
                else:
                    _CACHE.move_to_end(cache_key)
                    _LOGGER.info("Fabric price analysis cache hit")
                _LOGGER.info("Fabric price source completed in %.3fs", time.perf_counter() - analyze_started)
                return _CACHE[cache_key]
        except FabricPriceSourceError:
            raise
        except R2ObjectError as exc:
            raise FabricPriceSourceError(
                f"The R2 fabric price snapshot could not be read safely ({exc.failure_code})."
            ) from None
        except R2ConfigurationError:
            raise FabricPriceSourceError("R2 fabric price access is not configured correctly.") from None
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
            search_text = _normalize(" ".join((
                row.get("product_name", ""), row.get("category", ""), row.get("composition", ""),
                row.get("construction_or_finish", ""),
            )))
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
            meter_details = {
                id(row): _per_meter_price(row, amount) for row, amount in entries
            }
            per_meter_rows = [detail for detail in meter_details.values() if detail is not None]
            per_meter = [detail["value"] for detail in per_meter_rows]
            per_meter_statuses = {detail["status"] for detail in per_meter_rows}
            confidence_counts: dict[str, int] = {}
            for detail in per_meter_rows:
                confidence = detail["confidence"]
                confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
            comparables = []
            for row, amount in entries:
                meter_detail = meter_details[id(row)]
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
                    "unit_price_krw_per_meter": float(meter_detail["value"]) if meter_detail else None,
                    "unit_price_estimation_status": meter_detail["status"] if meter_detail else (
                        row.get("unit_price_estimation_status", "").strip() or None
                    ),
                    "unit_price_estimation_method": meter_detail["method"] if meter_detail else (
                        row.get("unit_price_estimation_method", "").strip() or None
                    ),
                    "unit_price_estimation_confidence": meter_detail["confidence"] if meter_detail else (
                        row.get("unit_price_estimation_confidence", "").strip() or None
                    ),
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
                "statistic": "unweighted_median_of_observed_source_listing_prices; units are preserved per row",
                },
                "price_per_meter_krw_statistics": ({
                    "sample_count": len(per_meter), "minimum": float(min(per_meter)),
                    "median": float(median(per_meter)), "maximum": float(max(per_meter)),
                    "currency": "KRW", "unit": "KRW/m",
                    "status": "estimated" if "estimated_not_verified" in per_meter_statuses else "observed",
                    "confidence_counts": confidence_counts,
                    "method": "Source-provided unit-price values or listed price divided by the source/recognized cut length; see per-record status and method.",
                } if per_meter else None),
                "estimated_price_per_meter_available": bool(per_meter),
                "verified_price_per_meter_available": "observed" in per_meter_statuses,
                "cost_per_meter_operational_eligible": False,
                "category_mapping": {
                    "mapping_id": "project07-fabric-material-terms",
                    "mapping_version": "1.1",
                    "matching": "normalized material term substring against product name, category, composition, or construction",
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
                    "Per-meter values marked estimated_not_verified are derived from listed prices and the source or recognized cut length; they are estimates, not observed unit prices.",
                    "Public retail listing prices are not automatically included in product margin costing.",
                    "Material percentages and some textile specifications are missing or unverified.",
                    "Shipping terms are separate and must not be treated as included in the listed price.",
                    "The snapshot has no prior history, so price trends and changes cannot be calculated.",
                ],
            })
        return tuple(results)
