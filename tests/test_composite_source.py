from __future__ import annotations

import unittest

from market_research_agent import (
    CompositeMarketResearchSource,
    EvidenceRecord,
    FindingKind,
    MarketResearchAgent,
    MarketResearchContext,
    PermissionStatus,
    ResearchFinding,
    ResultStatus,
    SourceReport,
)


class FixedSource:
    def __init__(self, report: SourceReport | None = None, error: Exception | None = None):
        self.report = report
        self.error = error
        self.received_context: MarketResearchContext | None = None

    def research(self, context: MarketResearchContext) -> SourceReport:
        self.received_context = context
        if self.error:
            raise self.error
        assert self.report is not None
        return self.report


def context() -> MarketResearchContext:
    return MarketResearchContext(
        request_id="req-1",
        planning_request={"query": "women's spring knitwear"},
        trend_result={"status": "ok"},
        inventory_product_result={"status": "ok"},
    )


def supported_report(prefix: str) -> SourceReport:
    evidence_id = f"{prefix}:E-1"
    evidence = EvidenceRecord(
        evidence_id=evidence_id,
        source_id=f"{prefix}-source",
        source_type="approved_dataset",
        source_name=f"Approved source {prefix}",
        locator="record-1",
        fetched_at="2026-11-06T00:00:00Z",
        permission_status=PermissionStatus.AUTHORIZED,
    )
    finding = ResearchFinding(
        finding_id=f"{prefix}:F-1",
        topic="category context",
        statement=f"Supported observation from {prefix}.",
        kind=FindingKind.FACT,
        evidence_ids=(evidence_id,),
    )
    return SourceReport(
        status=ResultStatus.OK,
        findings=(finding,),
        evidence=(evidence,),
    )


class CompositeSourceTests(unittest.TestCase):
    def test_combines_authorized_reports_and_preserves_evidence(self) -> None:
        first = FixedSource(supported_report("source-a"))
        second = FixedSource(supported_report("source-b"))
        request = context()

        result = MarketResearchAgent(
            CompositeMarketResearchSource([first, second])
        ).run(request)

        self.assertEqual(result.status, ResultStatus.OK)
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(len(result.evidence), 2)
        self.assertEqual(first.received_context, request)
        self.assertEqual(second.received_context, request)

    def test_source_failure_keeps_no_results_as_partial_coverage(self) -> None:
        empty = FixedSource(SourceReport(status=ResultStatus.NO_RESULTS))
        failed = FixedSource(error=TimeoutError("private provider detail"))

        result = MarketResearchAgent(
            CompositeMarketResearchSource([empty, failed])
        ).run(context())

        self.assertEqual(result.status, ResultStatus.PARTIAL)
        self.assertEqual(result.findings, ())
        self.assertIn("TIMEOUT", {error.code for error in result.errors})
        self.assertFalse(
            any("private provider detail" in error.message for error in result.errors)
        )

    def test_all_source_failures_return_error(self) -> None:
        result = MarketResearchAgent(
            CompositeMarketResearchSource(
                [FixedSource(error=PermissionError()), FixedSource(error=RuntimeError())]
            )
        ).run(context())

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(
            {error.code for error in result.errors}, {"FORBIDDEN", "SOURCE_UNAVAILABLE"}
        )

    def test_empty_source_set_is_explicit_no_results(self) -> None:
        result = MarketResearchAgent(CompositeMarketResearchSource()).run(context())

        self.assertEqual(result.status, ResultStatus.NO_RESULTS)
        self.assertIn("No authorized", result.limitations[0])


if __name__ == "__main__":
    unittest.main()
