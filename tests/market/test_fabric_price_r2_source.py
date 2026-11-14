from __future__ import annotations

import csv
import hashlib
import io
import os
import unittest
from unittest.mock import patch

from market_research_agent.sources.market.fabric_price_r2_source import (
    FabricPriceSourceError,
    R2FabricPriceSource,
)
from market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source import HmCombinedResearchSource
from market_research_agent.sources.shared.r2_object_store import R2ObjectStore, R2Settings


_COLUMNS = (
    "record_id", "product_name", "category", "composition", "construction_or_finish",
    "weight_g_per_cut", "width_cm", "color", "original_unit", "price_amount", "currency",
    "supplier", "shipping_terms", "source_url", "observed_at", "price_history_status",
    "source_page_type", "estimated_sales_unit", "estimated_length_m",
    "unit_price_estimation_status", "unit_price_estimation_method",
    "unit_price_estimation_confidence", "unit_price_estimation_evidence_urls",
)


def _csv_bytes() -> bytes:
    row = {name: "" for name in _COLUMNS}
    row.update({
        "record_id": "synthetic-fabric-001",
        "product_name": "Synthetic cotton fabric",
        "category": "면직물",
        "composition": "cotton 100%",
        "price_amount": "12000",
        "currency": "KRW",
        "original_unit": "1마",
        "source_url": "https://example.invalid/synthetic-listing",
        "observed_at": "2026-11-14",
        "unit_price_krw_per_meter": "13333",
        "unit_price_estimation_status": "estimated_not_verified",
        "unit_price_estimation_confidence": "low",
    })
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=(*_COLUMNS, "unit_price_krw_per_meter"))
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


class FakeR2Client:
    def __init__(self, payload: bytes | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        if self.error:
            raise self.error
        assert self.payload is not None
        return {"ContentLength": len(self.payload), "ETag": '"synthetic-v1"'}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        if self.error:
            raise self.error
        assert self.payload is not None
        return {
            "ContentLength": len(self.payload),
            "Metadata": {"sha256": hashlib.sha256(self.payload).hexdigest()},
            "Body": io.BytesIO(self.payload),
        }


def _store(client: FakeR2Client) -> R2ObjectStore:
    settings = R2Settings("synthetic-account", "synthetic-bucket", "synthetic-key", "synthetic-secret")
    return R2ObjectStore(settings, client)


class FabricPriceR2SourceTests(unittest.TestCase):
    def setUp(self) -> None:
        from market_research_agent.sources.market import fabric_price_r2_source
        with fabric_price_r2_source._CACHE_LOCK:
            fabric_price_r2_source._CACHE.clear()
            fabric_price_r2_source._OBJECT_CACHE.clear()

    def test_valid_synthetic_snapshot_is_parsed_and_matched(self):
        payload = _csv_bytes()
        client = FakeR2Client(payload)
        result = R2FabricPriceSource(
            _store(client), object_key="synthetic/fabric.csv",
        ).analyze({"material": "cotton"})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sample_count"], 1)
        self.assertEqual(result[0]["source_object"]["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result[0]["sources"][0]["unit_price_krw_per_meter"], 13333.0)
        self.assertEqual([kind for kind, _kwargs in client.calls], ["head", "get", "head"])

    def test_korean_fiber_aliases_match_without_misclassifying_modal_as_wool(self):
        from market_research_agent.sources.market.fabric_price_r2_source import _material_terms

        self.assertIn("린넨", _material_terms("리넨"))
        self.assertIn("polyester", _material_terms("폴리에스테르"))
        self.assertIn("wool", _material_terms("모"))
        self.assertNotIn("모", _material_terms("모달"))

    def test_intentionally_unconfigured_r2_is_no_result_without_read_error(self):
        with patch.dict(os.environ, {}, clear=True):
            result = R2FabricPriceSource(object_key="synthetic/fabric.csv").analyze(
                {"material": "cotton"},
            )
        self.assertEqual(result, ())

    def test_unsafe_manifest_key_is_rejected_before_r2_request(self):
        client = FakeR2Client(_csv_bytes())
        source = R2FabricPriceSource(_store(client), object_key="../unsafe.csv")
        with self.assertRaisesRegex(FabricPriceSourceError, "object key is invalid"):
            source.analyze({"material": "cotton"})
        self.assertEqual(client.calls, [])

    def test_combined_source_keeps_intentionally_unconfigured_fabric_as_no_result(self):
        with patch.dict(os.environ, {"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "false"}, clear=True):
            result = HmCombinedResearchSource().analyze({"category": "Outerwear", "material": "cotton"})
        self.assertEqual(result["fabric_prices"], ())
        self.assertIsNone(result["fabric_price_error"])
        self.assertEqual(result["fabric_price_source_status"], "not_configured")
        self.assertIsNone(result["historical_price"])
        self.assertIsNone(result["customer_age_profile"])

    def test_configured_snapshot_with_no_match_has_no_read_error(self):
        env = {
            "R2_ACCOUNT_ID": "synthetic-account", "R2_BUCKET": "synthetic-bucket",
            "R2_ACCESS_KEY_ID": "synthetic-key", "R2_SECRET_ACCESS_KEY": "synthetic-secret",
            "VOGUE_HM_RESEARCH_NONCOMMERCIAL": "false",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.R2FabricPriceSource.analyze",
            return_value=(),
        ):
            result = HmCombinedResearchSource().analyze({"category": "Outerwear", "material": "silk"})
        self.assertEqual(result["fabric_price_source_status"], "no_match")
        self.assertIsNone(result["fabric_price_error"])

    def test_partial_r2_configuration_is_reported_as_configuration_failure(self):
        with patch.dict(os.environ, {"R2_ACCOUNT_ID": "synthetic-account"}, clear=True):
            with self.assertRaisesRegex(FabricPriceSourceError, "configured incompletely"):
                R2FabricPriceSource(object_key="synthetic/fabric.csv").analyze({"material": "cotton"})

    def test_configured_but_missing_or_forbidden_object_is_read_error(self):
        for failure in (FileNotFoundError("synthetic object missing"), PermissionError("synthetic access denied")):
            with self.subTest(failure=type(failure).__name__):
                source = R2FabricPriceSource(
                    _store(FakeR2Client(error=failure)),
                    object_key=f"synthetic/{type(failure).__name__}.csv",
                )
                with self.assertRaisesRegex(FabricPriceSourceError, "metadata could not be read") as raised:
                    source.analyze({"material": "cotton"})
                self.assertNotIn(str(failure), str(raised.exception))

    def test_corrupt_and_unsupported_snapshots_are_not_treated_as_no_match(self):
        for payload, expected in (
            (b"\xff\xfe", "valid UTF-8 CSV"),
            (b"record_id,category\nx,y\n", "schema is unsupported"),
        ):
            with self.subTest(expected=expected):
                source = R2FabricPriceSource(
                    _store(FakeR2Client(payload)), object_key=f"synthetic/{hashlib.sha256(payload).hexdigest()}.csv",
                )
                with self.assertRaisesRegex(FabricPriceSourceError, expected):
                    source.analyze({"material": "cotton"})

    def test_valid_snapshot_with_no_material_match_returns_empty_result(self):
        result = R2FabricPriceSource(
            _store(FakeR2Client(_csv_bytes())), object_key="synthetic/no-match.csv",
        ).analyze({"material": "silk"})
        self.assertEqual(result, ())

    def test_knit_material_matches_recorded_construction_without_inventing_composition(self):
        reader = csv.DictReader(io.StringIO(_csv_bytes().decode("utf-8")))
        row = next(reader)
        row["construction_or_finish"] = "knit construction"
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerow(row)
        payload = buffer.getvalue().encode("utf-8")
        result = R2FabricPriceSource(
            _store(FakeR2Client(payload)), object_key="synthetic/knit.csv",
        ).analyze({"category": "니트웨어", "material": "니트"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sources"][0]["composition"], "cotton 100%")
        self.assertEqual(result[0]["sources"][0]["construction_or_finish"], "knit construction")

    def test_knit_material_matches_listing_name_when_construction_is_not_populated(self):
        reader = csv.DictReader(io.StringIO(_csv_bytes().decode("utf-8")))
        row = next(reader)
        row["product_name"] = "니트 원단 합성 listing"
        row["construction_or_finish"] = ""
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerow(row)
        payload = buffer.getvalue().encode("utf-8")
        result = R2FabricPriceSource(
            _store(FakeR2Client(payload)), object_key="synthetic/knit-title.csv",
        ).analyze({"category": "니트웨어", "material": "니트"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sources"][0]["product_name"], "니트 원단 합성 listing")
        self.assertEqual(result[0]["sources"][0]["composition"], "cotton 100%")


if __name__ == "__main__":
    unittest.main()
