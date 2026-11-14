"""Repeatable offline serial-vs-parallel P07 latency benchmark.

The fixed delays model source and opt-in LLM latency; no R2 or LLM service is
called. Run with: PYTHONPATH=src python3 tests/benchmark_sales_market_latency.py
"""

from __future__ import annotations

import json
import os
import statistics
import time
from unittest.mock import patch

from market_research_agent import build_sales_market_review
from market_research_agent.http_server import context_from_review_request
from market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source import HmCombinedResearchSource


REQUEST = {
    "contract_version": "vogue-sales-market-review/0.3-proposal",
    "request_id": "benchmark:synthetic:1",
    "input": {
        "request_id": "benchmark:synthetic:1",
        "product": {
            "category": "Outerwear", "product_type": "Coat", "gender": "women",
            "season": "winter", "material": "wool",
        },
        "product_evidence": {
            field: {"kind": "user_requested", "source_ref": f"request.product.{field}", "evidence_ids": []}
            for field in ("category", "product_type", "gender", "season", "material")
        },
    },
}
AGE_PROFILE = {
    "source_id": "synthetic-hm", "permission_status": "non_commercial_research_only",
    "operational_eligible": False,
    "target_age_cohort": {"age_band": "20_39", "suppressed": False}, "age_bands": [],
}
HISTORICAL_PRICE = {
    "source_id": "synthetic-hm", "source_type": "historical_kaggle_transaction_price_aggregate",
    "permission_status": "non_commercial_research_only", "operational_eligible": False,
    "sample_count": 120, "price_statistics": {"median": 0.03, "currency": None},
}
FABRIC_PRICES = ({
    "source_type": "synthetic_public_listing", "source_grade": "public_sale",
    "listed_price_statistics": {"median": 9000, "currency": "KRW"},
    "cost_per_meter_operational_eligible": False,
},)
EXPECTED_SECTIONS = (
    "status", "draft_context", "category_price_comparison", "material_cost_analysis",
    "customer_segment_analysis", "margin_estimate", "target_audience_analysis", "errors", "missing_data",
)


def _fabric(product):
    time.sleep(0.020)
    return FABRIC_PRICES


def _age(_self, _category, *, gender=None):
    time.sleep(0.030)
    return AGE_PROFILE


def _price(_self, _category, *, gender=None, product_type=None, season=None):
    time.sleep(0.045)
    return HISTORICAL_PRICE


def _run(*, parallel: bool) -> dict[str, object]:
    started = time.perf_counter()
    request_id, product = context_from_review_request(json.loads(json.dumps(REQUEST)))
    validation = time.perf_counter() - started
    patches = (
        patch.dict(os.environ, {"VOGUE_HM_RESEARCH_NONCOMMERCIAL": "true"}),
        patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.R2FabricPriceSource.analyze", side_effect=_fabric),
        patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmCustomerAgeResearchSource.profile", _age),
        patch("market_research_agent.sources.sales.hm_kaggle.hm_combined_research_source.HmHistoricalPriceResearchSource.analyze", _price),
    )
    for item in patches:
        item.start()
    try:
        started = time.perf_counter()
        if parallel:
            bundle = HmCombinedResearchSource().analyze(product)
        else:
            bundle = {
                "fabric_prices": _fabric(product), "fabric_price_error": None,
                "fabric_price_source_status": "available",
                "customer_age_profile": _age(None, product["category"], gender=product["gender"]),
                "customer_age_error": None,
                "historical_price": _price(
                    None, product["category"], gender=product["gender"],
                    product_type=product["product_type"], season=product["season"],
                ),
                "historical_price_error": None,
            }
        research = time.perf_counter() - started
    finally:
        for item in reversed(patches):
            item.stop()

    started = time.perf_counter()
    target_hypothesis = {"primary_segment": {"description": "synthetic hypothesis"}}
    time.sleep(0.025)  # fixed, non-billable LLM stage; depends on source outputs
    llm = time.perf_counter() - started

    started = time.perf_counter()
    review = build_sales_market_review(
        request_id, product_context=product,
        fabric_price_analysis=bundle["fabric_prices"],
        fabric_price_error=bundle["fabric_price_error"],
        fabric_price_source_status=bundle["fabric_price_source_status"],
        historical_price_analysis=bundle["historical_price"],
        historical_price_error=bundle["historical_price_error"],
        customer_age_profile=bundle["customer_age_profile"],
        customer_age_error=bundle["customer_age_error"],
        target_audience_proposal=target_hypothesis,
    ).to_dict()
    response = {
        "contract_version": "vogue-sales-market-review/0.3-proposal", "request_id": request_id,
        "producer": {"agent_id": "sales-market-agent", "schema_version": review["schema_version"]},
        "status": review["status"], "result": review,
    }
    encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
    output = time.perf_counter() - started
    return {
        "validation": validation, "research": research, "llm": llm,
        "response_build_serialize": output,
        "total": validation + research + llm + output,
        "review_subset": {key: review.get(key) for key in EXPECTED_SECTIONS} | {
            "request_id": response["request_id"], "envelope_status": response["status"],
        },
        "encoded_size": len(encoded),
    }


def main() -> None:
    samples: dict[str, list[dict[str, object]]] = {"serial": [], "parallel": []}
    warmups, repetitions = 2, 7
    for mode in samples:
        for iteration in range(warmups + repetitions):
            measured = _run(parallel=mode == "parallel")
            if iteration >= warmups:
                samples[mode].append(measured)
    baseline = samples["serial"][0]["review_subset"]
    assert all(row["review_subset"] == baseline for group in samples.values() for row in group)
    for stage in ("validation", "research", "llm", "response_build_serialize", "total"):
        serial = statistics.median(float(row[stage]) for row in samples["serial"]) * 1000
        parallel = statistics.median(float(row[stage]) for row in samples["parallel"]) * 1000
        print(f"{stage}: serial_p50={serial:.2f}ms parallel_p50={parallel:.2f}ms delta={serial - parallel:.2f}ms")
    print(f"repetitions={repetitions} warmups={warmups} identical_review_output=true encoded_bytes={samples['serial'][0]['encoded_size']}")
    print("fixed delays: fabric=20ms age=30ms price=45ms target_hypothesis=25ms; external calls=0")


if __name__ == "__main__":
    main()
