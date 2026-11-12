#!/usr/bin/env python3
"""Build the bundled synthetic review fixture from the seed-407 query coverage generator.

Package data for VOGUE_P07_REVIEW_SOURCE=synthetic_local_fixture. The rows are
copied verbatim from scripts/generate_query_coverage_fixture.py output under
the restore configuration (seed 407). ``--check`` regenerates them offline and
fails if the committed bundle differs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from generate_query_coverage_fixture import GENERATOR_VERSION, MARKERS, generate
from restore_test_fixtures import QUERY_COVERAGE_CONFIG

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/market_research_agent/resources/synthetic_review_fixture/bundle.json"
SCHEMA_VERSION = "project07-synthetic-review-fixture/1.0"
SELECTION = {
    "category": "knitwear", "gender": "women", "geography": "KR", "channel": "online",
    "months": ["2027-03", "2027-04", "2027-05"], "market_color": "black",
}
NOTICE = (
    "Verbatim excerpt of the deterministic seed-407 synthetic query coverage fixture. "
    "Generated test values only: not VOGUE or H&M sales, not observed market data, not a forecast, "
    "and not scoped to the category or season of any reviewed Draft."
)


def _rows(directory: Path) -> list[dict]:
    return [json.loads(line) for path in sorted(directory.glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _selected(row: dict) -> bool:
    return (all(row.get(key) == SELECTION[key] for key in ("category", "gender", "geography", "channel"))
            and row["period_start"][:7] in SELECTION["months"])


def build_bytes() -> bytes:
    with tempfile.TemporaryDirectory(prefix="p07-review-fixture-") as temp:
        root = Path(temp)
        (root / "config.json").write_text(json.dumps(QUERY_COVERAGE_CONFIG), encoding="utf-8")
        manifest = generate(root / "config.json", root / "generated")
        sales = _rows(root / "generated/synthetic_sales_observations")
        market = _rows(root / "generated/market_observations")
        evidence = {item["evidence_id"]: item for item in _rows(root / "generated/evidence")}
    sales_rows = sorted((row for row in sales if _selected(row)), key=lambda row: row["period_start"])
    market_rows = sorted((row for row in market if _selected(row)
                          and row["attributes"]["color"] == SELECTION["market_color"]),
                         key=lambda row: row["period_start"])
    if not len(sales_rows) == len(market_rows) == len(SELECTION["months"]):
        raise SystemExit("Generator output no longer contains exactly one row per selected month.")
    evidence_ids = dict.fromkeys(eid for row in (*sales_rows, *market_rows) for eid in row["evidence_ids"])
    config_bytes = json.dumps(QUERY_COVERAGE_CONFIG, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    bundle = {
        "schema_version": SCHEMA_VERSION, **MARKERS,
        "source_mode": "synthetic_local_fixture",
        "generator": {
            "script": "scripts/generate_query_coverage_fixture.py", "generator_version": GENERATOR_VERSION,
            "seed": manifest["seed"], "request_id": manifest["request_id"],
            "config_sha256": hashlib.sha256(config_bytes.encode("utf-8")).hexdigest(),
            "builder": "scripts/build_synthetic_review_fixture.py",
        },
        "selection": SELECTION,
        "notice": NOTICE,
        "sales_like": sales_rows,
        "market_like": market_rows,
        "evidence": [evidence[eid] for eid in evidence_ids],
    }
    return (json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the committed bundle differs.")
    args = parser.parse_args(argv)
    data = build_bytes()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != data:
            raise SystemExit("Synthetic review fixture is stale; run scripts/build_synthetic_review_fixture.py.")
        print(f"Synthetic review fixture matches seed-407 generator output (sha256 {hashlib.sha256(data).hexdigest()}).")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(data)
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(data)} bytes); external calls=0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
