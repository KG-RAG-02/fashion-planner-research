"""오프라인 합성 리뷰 소스.

RESEARCH_REVIEW_SOURCE=synthetic이면 R2, DuckDB, 환율 API, LLM을 부르지 않고
패키지의 resources/fixtures/synthetic-review.json에서 리뷰 재료를 만든다.
통합 테스트(stub)에서 기획서를 끝까지 그려 보기 위한 기능이며, 결과의 자료원은
모두 synthetic_test_data로 표시한다.

응답 모양이 실제와 같도록, 합성 집계 행을 실제 스냅샷 빌더(build_snapshot_payload)에
넣고 실제 선택 로직(HmResearchAggregateSource._select)으로 상품에 맞는 값을 고른다.
같은 상품 입력이면 같은 결과가 나온다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from typing import Any

from ..llm.target_audience import propose_target_audience
from ..resource_paths import CONFIGS_DIR, RESOURCES_DIR
from .sales.hm_kaggle.hm_customer_age_source import _map_product_type, _map_requested_category
from .sales.hm_kaggle.hm_research_aggregate import HmResearchAggregateSource, _mapping_digest, build_snapshot_payload

REVIEW_SOURCE_ENV = "RESEARCH_REVIEW_SOURCE"
SYNTHETIC = "synthetic"
SOURCE_ID = "synthetic-research-fixture"
SOURCE_TYPE = "synthetic_test_data"
PERMISSION_STATUS = "synthetic_test_data"
SOURCE_NAME = "합성(synthetic) 리뷰 자료"
LLM_CALL_STATUS = "synthetic_fixture"

_FIXTURE_PATH = RESOURCES_DIR / "fixtures" / "synthetic-review.json"
_FABRIC_COLUMNS = (
    "record_id", "product_name", "category", "composition", "construction_or_finish",
    "weight_g_per_cut", "width_cm", "color", "original_unit", "price_amount", "currency",
    "supplier", "shipping_terms", "source_url", "observed_at", "price_history_status",
    "source_page_type", "estimated_sales_unit", "estimated_length_m",
    "unit_price_estimation_status", "unit_price_estimation_method",
    "unit_price_estimation_confidence", "unit_price_estimation_evidence_urls",
    "unit_price_krw_per_meter",
)
_AGE_BANDS = ("under_20", "20_29", "30_39", "40_49", "50_plus", "unknown_age")


class UnsupportedReviewSource(ValueError):
    """RESEARCH_REVIEW_SOURCE에 지원하지 않는 값이 들어 있다."""


def configured_review_source() -> str | None:
    """설정된 리뷰 소스를 돌려준다. 비었으면 None(기본 자료원)."""
    value = os.getenv(REVIEW_SOURCE_ENV, "").strip()
    if not value:
        return None
    if value == SYNTHETIC:
        return SYNTHETIC
    raise UnsupportedReviewSource(f"{REVIEW_SOURCE_ENV} supports only '{SYNTHETIC}'.")


def _fixture() -> tuple[dict[str, Any], str]:
    raw = _FIXTURE_PATH.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _candidate_categories(product: dict[str, Any], mapping: dict[str, Any]) -> list[str]:
    """_select가 연령·가격 행을 찾는 카테고리 후보(순서 유지, 중복 제거)."""
    requested = str(product.get("category", "")).strip()
    mapped = _map_requested_category(requested, mapping).get("mapped_category")
    by_type = _map_product_type(product.get("product_type"), mapping).get("mapped_category")
    return list(dict.fromkeys(
        value.strip() for value in (by_type, mapped, requested) if isinstance(value, str) and value.strip()
    ))


def _aggregate_rows(fixture: dict[str, Any], categories: list[str]) -> list[tuple[Any, ...]]:
    """스냅샷 빌더가 받는 DuckDB 결과 행 모양으로 합성 집계를 만든다."""
    period = fixture["period"]
    shares = fixture["segment_shares"]
    channels = list(fixture["channel_codes"])
    rows: list[tuple[Any, ...]] = []
    for category in categories:
        values = fixture["categories"].get(category, fixture["categories"]["default"])
        price = values["price"]
        statistics = tuple(price[key] for key in ("minimum", "p25", "median", "p75", "maximum", "mean"))
        segments = {"__all__": 1.0, **shares}
        for segment, share in segments.items():
            for band in _AGE_BANDS:
                count = round(values["age_bands"][band] * share)
                rows.append((category, segment, "age", band, count, period["start"], period["end"],
                             0, 0, None, None, None, None, None, None, [], None))
            rows.append((category, segment, "price", None, 0, period["start"], period["end"],
                         round(price["transaction_rows"] * share), round(price["article_count"] * share),
                         *statistics, channels, None))
    return rows


def _fabric_csv(fixture: dict[str, Any], material: str | None) -> str | None:
    """요청 소재 이름으로 합성 판매 목록 행을 만든다(소재가 없으면 None)."""
    if not isinstance(material, str) or not material.strip():
        return None
    material = material.strip()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_FABRIC_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for index, listing in enumerate(fixture["fabric_listings"], start=1):
        writer.writerow({
            "record_id": f"synthetic-fabric-{index:03d}",
            "product_name": f"[합성] {material} {listing['label']} 원단",
            "category": "합성 원단",
            "composition": material,
            "construction_or_finish": listing["label"],
            "weight_g_per_cut": listing["weight_g_per_cut"],
            "width_cm": listing["width_cm"],
            "original_unit": listing["original_unit"],
            "price_amount": str(listing["price_amount"]),
            "currency": "KRW",
            "supplier": "합성 원단 예시",
            "source_url": f"https://example.com/synthetic-fabric/{index:03d}",
            "observed_at": fixture["period"]["end"],
            "price_history_status": "synthetic",
            "source_page_type": SOURCE_TYPE,
        })
    return buffer.getvalue()


def _relabel(record: dict[str, Any], notice: str, *, part: str, version: str) -> dict[str, Any]:
    # 자료 종류마다 locator를 나눠 근거 ID가 서로 다르게 한다(supports_fields가 섞이지 않게).
    return {
        **record,
        "source_id": SOURCE_ID, "source_type": SOURCE_TYPE, "source_name": SOURCE_NAME,
        "permission_status": PERMISSION_STATUS, "operational_eligible": False,
        "evidence_interpretation": notice,
        "limitations": [notice],
        "sources": [{"object_key": f"synthetic://fashion-planner-research/review-fixture/{part}", "version": version}],
    }


def synthetic_research_bundle(product: dict[str, Any]) -> dict[str, Any]:
    """HmCombinedResearchSource.analyze와 같은 모양의 합성 자료 묶음."""
    fixture, fixture_sha256 = _fixture()
    mapping = json.loads((CONFIGS_DIR / "hm-category-map.json").read_text(encoding="utf-8"))
    source_ref = {"object_key": "synthetic://fashion-planner-research/review-fixture", "version": fixture_sha256}
    fabric_csv = _fabric_csv(fixture, product.get("material"))
    fabric_ref = None
    if fabric_csv is not None:
        fabric_ref = {
            "object_key": "synthetic://fashion-planner-research/fabric-listings",
            "version": fixture_sha256,
            "sha256": hashlib.sha256(fabric_csv.encode("utf-8")).hexdigest(),
        }
    snapshot = build_snapshot_payload(
        _aggregate_rows(fixture, _candidate_categories(product, mapping)),
        source_refs=(source_ref,), mapping=mapping, mapping_fingerprint=_mapping_digest(mapping),
        fabric_source_ref=fabric_ref, fabric_listing_csv=fabric_csv,
        aggregation_version=f"synthetic-review-fixture;sha256={fixture_sha256}",
    )
    bundle = HmResearchAggregateSource._select(snapshot, product)
    notice = fixture["notice"]
    if bundle["customer_age_profile"] is not None:
        bundle["customer_age_profile"] = _relabel(
            bundle["customer_age_profile"], notice, part="age-bands", version=fixture_sha256)
    if bundle["historical_price"] is not None:
        bundle["historical_price"] = _relabel(
            bundle["historical_price"], notice, part="price-statistics", version=fixture_sha256)
    bundle["fabric_prices"] = tuple(
        {**group, "source_type": SOURCE_TYPE, "source_grade": SYNTHETIC, "source_name": SOURCE_NAME,
         "permission_status": PERMISSION_STATUS, "evidence_interpretation": notice}
        for group in bundle["fabric_prices"]
    )
    return bundle


def synthetic_fx_reference() -> dict[str, Any]:
    """네트워크 없이 쓰는 고정 합성 환율(날짜 계산 없음)."""
    fixture, _ = _fixture()
    return {
        "base_currency": "USD",
        "quote_currency": "KRW",
        "rate": float(fixture["usd_krw"]["rate"]),
        "rate_date": fixture["usd_krw"]["rate_date"],
        "provider": "synthetic fixed rate",
        "source_url": "synthetic://fashion-planner-research/fixed-usd-krw-rate",
        "rate_type": "synthetic_fixed_rate",
        "fallback_used": False,
    }


def synthetic_target_audience(
    *, product_context: dict[str, Any], customer_profile: dict[str, Any] | None,
    fabric_listing_reference: list[dict[str, Any]],
) -> dict[str, Any]:
    """LLM 없이 고정 템플릿으로 타깃 가설을 만들고 실제 출력 검증을 통과시킨다."""
    fixture, _ = _fixture()
    labels = fixture["target_audience"]
    cohort = (customer_profile or {}).get("target_age_cohort") or {}
    age = labels["cohort_labels"].get(cohort.get("age_band"), "연령 미상")
    gender = labels["gender_labels"].get(product_context.get("gender") or "", "")
    who = f"{age} {gender}".strip()
    category = str(product_context.get("category") or "상품")
    season = product_context.get("season")
    season_text = f" {season} 시즌" if season else ""
    output = {
        "proposal": f"[합성] {category}{season_text} 상품은 {who} 고객을 먼저 검토하는 예시 가설입니다.",
        "primary_segment": f"[합성] {who} 고객",
        "secondary_segments": ["[합성] 인접 연령대 고객"],
        "age_evidence": f"[합성] 합성 연령 집계에서 {age} 비중이 가장 큽니다.",
        "product_fit": {
            "style_direction": "[합성] 기본 실루엣 중심",
            "wearing_occasions": ["[합성] 출근", "[합성] 주말 외출"],
            "needs_hypotheses": ["[합성] 활용도", "[합성] 관리 편의"],
            "styling_cues": ["[합성] 단색 매치", "[합성] 레이어링"],
        },
        "merchandising_actions": ["[합성] 기본 색상 우선 구성", "[합성] 소량 선발주"],
        "channel_content_hypotheses": ["[합성] 착용 장면 사진", "[합성] 소재 설명 카드"],
        "validation_plan": ["[합성] 실제 판매 자료로 다시 검토", "[합성] 고객 설문", "[합성] 소량 판매 시험"],
        "evidence_basis": ["[합성] 합성 연령 집계", "[합성] 합성 원단 목록"],
        "limitations": [fixture["notice"], "[합성] 모델이 만든 문장이 아닙니다."],
    }
    result = propose_target_audience(
        product_context=product_context, customer_profile=customer_profile,
        fabric_listing_reference=fabric_listing_reference,
        call=lambda **_: {"output": output},
    )
    result["provider"] = LLM_CALL_STATUS
    return result


def apply_synthetic_limitations(review: dict[str, Any]) -> None:
    """실제 자료원을 가리키는 한계 문구를 합성 표시로 바꾼다. 마진 기준 문구는 남긴다."""
    fixture, _ = _fixture()
    kept = [item for item in review.get("limitations", [])
            if isinstance(item, str) and item.startswith("[마진 참고]")]
    review["limitations"] = [f"[합성 자료] {REVIEW_SOURCE_ENV}={SYNTHETIC}: {fixture['notice']}", *kept]
