"""Evaluate the deterministic query parser against test-only labeled examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_research_agent.query_interpretation import PARSER_VERSION, interpret_query

FIXTURE = Path("tests/fixtures/synthetic_market_rag/query-interpretation-cases.json")


def evaluate(path: Path = FIXTURE) -> dict[str, Any]:
    target = path.expanduser().resolve()
    if not {"tests", "fixtures", "synthetic_market_rag"}.issubset(target.parts):
        raise ValueError("Parser fixture must be under tests/fixtures/synthetic_market_rag.")
    bundle = json.loads(target.read_text(encoding="utf-8"))
    if (
        bundle.get("synthetic") is not True
        or bundle.get("data_nature") != "synthetic_test"
        or bundle.get("permission_status") != "authorized_for_synthetic_testing_only"
        or bundle.get("operational_eligible") is not False
    ):
        raise ValueError("Parser evaluation fixture must be marked synthetic_test.")
    cases = bundle.get("cases", [])
    rows = []
    for case in cases:
        actual = interpret_query(case["query"])
        expected = case["expected"]
        compared = {}
        for field, expected_value in expected.items():
            actual_value = actual.get(field)
            if isinstance(actual_value, tuple):
                actual_value = list(actual_value)
            compared[field] = actual_value == expected_value
        rows.append({
            "id": case["id"], "group": case["group"],
            "passed": all(compared.values()), "fields": compared,
            "actual": {field: actual.get(field) for field in expected},
        })
    clear_rows = [row for row in rows if row["group"] in {"clear_scope", "synonyms", "unsupported_metric"}]
    clear_total = len(clear_rows)
    groups: dict[str, dict[str, int]] = {}
    for row in rows:
        group = groups.setdefault(row["group"], {"count": 0, "passed": 0})
        group["count"] += 1
        group["passed"] += int(row["passed"])
    return {
        "synthetic": True, "data_nature": "synthetic_test",
        "permission_status": "authorized_for_synthetic_testing_only",
        "operational_eligible": False,
        "suite_version": bundle.get("suite_version"),
        "parser_version": PARSER_VERSION, "case_count": len(rows),
        "clear_intent_metric_accuracy": sum(row["passed"] for row in clear_rows) / max(1, clear_total),
        "scope_ambiguity_accuracy": sum(row["passed"] for row in rows if row["group"] in {"ambiguity", "period", "unresolved"}) / max(1, sum(row["group"] in {"ambiguity", "period", "unresolved"} for row in rows)),
        "groups": groups,
        "failed_case_ids": [row["id"] for row in rows if not row["passed"]],
        "cases": rows,
    }


def main() -> int:
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
