#!/usr/bin/env python3
"""Run the explicitly opted-in local synthetic RAG analysis proposal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from market_research_agent.synthetic_rag import (  # noqa: E402
    CanonicalRequestError,
    LIMITATION,
    SCHEMA_VERSION,
    SYNTHETIC_MARKERS,
    SyntheticFixtureError,
    analyze_synthetic_request,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("synthetic_test",), help="must explicitly opt into test-only fixtures")
    parser.add_argument("--request", required=True, type=Path, help="canonical structured request JSON")
    parser.add_argument("--output", type=Path, help="optional local JSON output path; defaults to stdout")
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        result = analyze_synthetic_request(request, mode=args.mode)
    except (OSError, json.JSONDecodeError, CanonicalRequestError, SyntheticFixtureError) as exc:
        error = {"schema_version": SCHEMA_VERSION, "status": "error", "reason": str(exc),
                 "execution_mode": "synthetic_test", **SYNTHETIC_MARKERS, "limitations": [LIMITATION]}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
