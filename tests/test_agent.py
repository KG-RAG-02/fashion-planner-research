from __future__ import annotations

import unittest

from market_research_agent import (
    AgentError,
    EvidenceRecord,
    FindingKind,
    MarketResearchAgent,
    MarketResearchContext,
    PermissionStatus,
    ResearchFinding,
    ResultStatus,
    SourceReport,
)


def make_context(*, product_concept: dict | None = None) -> MarketResearchContext:
    return MarketResearchContext(
        request_id="request-123",
        planning_request={"query": "spring knitwear"},
        trend_result={"status": "ok"},
        inventory_product_result={"status": "ok"},
        product_concept=product_concept,
    )


def make_evidence(
    evidence_id: str = "market:E-001",
    permission: PermissionStatus = PermissionStatus.AUTHORIZED,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_id="source-001",
        source_type="report",
        source_name="Approved market report",
        locator="section 2",
        published_at="2026-04-01T00:00:00Z",
        observed_at="2026-03-01T00:00:00Z",
        fetched_at="2026-11-06T00:00:00Z",
        permission_status=permission,
    )


class StaticSource:
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


class MarketResearchAgentTests(unittest.TestCase):
    def test_no_source_is_no_results_not_zero(self) -> None:
        result = MarketResearchAgent().run(make_context())

        self.assertEqual(result.status, ResultStatus.NO_RESULTS)
        self.assertEqual(result.findings, ())
        self.assertIn("No authorized", result.limitations[0])
        self.assertNotIn("0", result.summary)

    def test_fact_and_source_timestamps_are_preserved(self) -> None:
        report = SourceReport(
            status=ResultStatus.OK,
            findings=(
                ResearchFinding(
                    finding_id="finding-1",
                    topic="category context",
                    statement="The approved report describes the category.",
                    kind=FindingKind.FACT,
                    evidence_ids=("market:E-001",),
                ),
            ),
            evidence=(make_evidence(),),
        )
        result = MarketResearchAgent(StaticSource(report)).run(make_context())

        self.assertEqual(result.status, ResultStatus.OK)
        self.assertEqual(result.findings[0].evidence_ids, ("market:E-001",))
        self.assertEqual(result.evidence[0].observed_at, "2026-03-01T00:00:00Z")
        self.assertEqual(result.evidence[0].fetched_at, "2026-11-06T00:00:00Z")

    def test_unconfirmed_evidence_and_dependent_facts_are_omitted(self) -> None:
        report = SourceReport(
            status=ResultStatus.OK,
            findings=(
                ResearchFinding(
                    finding_id="finding-1",
                    topic="market",
                    statement="Unsupported market claim.",
                    kind=FindingKind.FACT,
                    evidence_ids=("market:E-001",),
                ),
            ),
            evidence=(make_evidence(permission=PermissionStatus.UNKNOWN),),
        )
        result = MarketResearchAgent(StaticSource(report)).run(make_context())

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.findings, ())
        self.assertEqual(result.evidence, ())
        self.assertIn("EVIDENCE_PERMISSION_UNCONFIRMED", {e.code for e in result.errors})

    def test_fact_without_evidence_is_not_returned(self) -> None:
        report = SourceReport(
            status=ResultStatus.OK,
            findings=(
                ResearchFinding(
                    finding_id="finding-1",
                    topic="market",
                    statement="A factual claim without a source.",
                    kind=FindingKind.FACT,
                ),
            ),
        )
        result = MarketResearchAgent(StaticSource(report)).run(make_context())

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.findings, ())
        self.assertIn("UNSUPPORTED_FINDING", {e.code for e in result.errors})

    def test_partial_source_report_keeps_available_findings(self) -> None:
        report = SourceReport(
            status=ResultStatus.PARTIAL,
            findings=(
                ResearchFinding(
                    finding_id="finding-1",
                    topic="market",
                    statement="A supported observation.",
                    kind=FindingKind.FACT,
                    evidence_ids=("market:E-001",),
                ),
            ),
            evidence=(make_evidence(),),
            limitations=("One requested source was unavailable.",),
            errors=(AgentError("SOURCE_UNAVAILABLE", "A source could not be reached.", True),),
        )
        result = MarketResearchAgent(StaticSource(report)).run(make_context())

        self.assertEqual(result.status, ResultStatus.PARTIAL)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(len(result.evidence), 1)

    def test_permission_and_timeout_errors_are_safe(self) -> None:
        denied = MarketResearchAgent(StaticSource(error=PermissionError("secret"))).run(
            make_context()
        )
        timed_out = MarketResearchAgent(StaticSource(error=TimeoutError("secret"))).run(
            make_context()
        )

        self.assertEqual(denied.errors[0].code, "FORBIDDEN")
        self.assertNotIn("secret", denied.errors[0].message)
        self.assertEqual(timed_out.errors[0].code, "TIMEOUT")
        self.assertTrue(timed_out.errors[0].retryable)

    def test_product_concept_is_optional_context_not_a_prerequisite(self) -> None:
        source = StaticSource(SourceReport(status=ResultStatus.NO_RESULTS))
        result = MarketResearchAgent(source).run(make_context(product_concept=None))

        self.assertIsNone(source.received_context.product_concept)
        self.assertEqual(result.request_id, "request-123")
        self.assertEqual(result.status, ResultStatus.NO_RESULTS)

    def test_empty_request_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MarketResearchAgent().run(
                MarketResearchContext(request_id=" ", planning_request={})
            )


if __name__ == "__main__":
    unittest.main()
