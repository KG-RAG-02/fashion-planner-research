#!/usr/bin/env python3
"""Run one explicitly selected Project_07 sales or market LLM analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from market_research_agent.llm.analysis import AnalysisInputError, run_analysis  # noqa: E402
from market_research_agent.llm.responses_api import LlmCallError, OpenAIResponsesClient  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", choices=("sales_analysis", "market_analysis"), required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--category", help="Optional exact substring filter for synthetic market category.")
    parser.add_argument("--metric", help="Optional exact synthetic market metric name filter.")
    parser.add_argument("--hm-usage-authorized", action="store_true", help="Explicit acknowledgement after verifying H&M usage AND transfer to the configured LLM provider.")
    parser.add_argument("--hm-permission-reference", help="Reference to the separately verified permission/terms review; this is not itself proof of permission.")
    parser.add_argument("--output", type=Path, help="Write JSON output here; defaults to stdout.")
    args = parser.parse_args(argv)

    try:
        client = OpenAIResponsesClient()
        result = run_analysis(
            domain=args.domain,
            query=args.query,
            request_id=args.request_id,
            category=args.category,
            metric=args.metric,
            hm_usage_acknowledged=args.hm_usage_authorized,
            hm_permission_reference=args.hm_permission_reference,
            client=client,
        )
    except (AnalysisInputError, LlmCallError, OSError, ValueError) as exc:
        print(f"LLM analysis stopped: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

