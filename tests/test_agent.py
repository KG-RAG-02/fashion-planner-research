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
    SalesDataResult,
    SalesObservation,
)


def make_context() -> MarketResearchContext:
    return MarketResearchContext(
        request_id="request-123",
        planning_request={"query": "spring knitwear"},
        trend_result={"status": "ok"},
        inventory_product_result={"status": "ok"},
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
    def test_project07_owns_scoped_sales_observations_and_checks_evidence(self) -> None:
        observation = SalesObservation(
            observation_id="sales-obs-1",
            metric_name="units_sold",
            value=0,
            unit="units",
            evidence_ids=("market:E-001",),
            category="women_knit",
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        source = StaticSource(SourceReport(
            status=ResultStatus.OK,
            evidence=(make_evidence(),),
            sales_data=SalesDataResult(status="ok", observations=(observation,)),
        ))
        result = MarketResearchAgent(source).run(make_context())

        self.assertEqual(result.sales_data.status, "ok")
        self.assertEqual(result.sales_data.observations[0].value, 0)
        self.assertEqual(result.to_proposal_dict()["sales_data"]["observations"][0]["evidence_ids"], ("market:E-001",))

    def test_sales_observation_without_authorized_evidence_is_omitted(self) -> None:
        observation = SalesObservation(
            observation_id="sales-obs-1",
            metric_name="units_sold",
            value=13,
            unit="units",
            evidence_ids=("missing:E-1",),
        )
        source = StaticSource(SourceReport(
            status=ResultStatus.OK,
            sales_data=SalesDataResult(status="ok", observations=(observation,)),
        ))
        result = MarketResearchAgent(source).run(make_context())

        self.assertEqual(result.sales_data.status, "partial")
        self.assertEqual(result.sales_data.observations, ())
        self.assertIn("INVALID_SALES_EVIDENCE", {item.code for item in result.errors})

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

    def test_evidence_without_traceable_source_identity_is_omitted(self) -> None:
        malformed = EvidenceRecord(
            evidence_id="market:E-001",
            source_id=" ",
            source_type="report",
            source_name="Approved market report",
            permission_status=PermissionStatus.AUTHORIZED,
        )
        result = MarketResearchAgent(
            StaticSource(SourceReport(status=ResultStatus.OK, evidence=(malformed,)))
        ).run(make_context())

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.evidence, ())
        self.assertIn("INVALID_EVIDENCE", {error.code for error in result.errors})

    def test_blank_finding_is_not_returned_as_a_claim(self) -> None:
        report = SourceReport(
            status=ResultStatus.OK,
            findings=(
                ResearchFinding(
                    finding_id="finding-1",
                    topic="market",
                    statement=" ",
                    kind=FindingKind.INTERPRETATION,
                ),
            ),
        )
        result = MarketResearchAgent(StaticSource(report)).run(make_context())

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.findings, ())
        self.assertIn("UNSUPPORTED_FINDING", {error.code for error in result.errors})

    def test_invalid_finding_kind_is_omitted_without_crashing(self) -> None:
        report = SourceReport(
            status=ResultStatus.OK,
            findings=(
                ResearchFinding(
                    finding_id="finding-1",
                    topic="market",
                    statement="Unclassified source claim.",
                    kind="fact",  # type: ignore[arg-type]
                ),
            ),
        )
        result = MarketResearchAgent(StaticSource(report)).run(make_context())

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.findings, ())
        self.assertIn("UNSUPPORTED_FINDING", {error.code for error in result.errors})

    def test_invalid_provider_status_returns_safe_error(self) -> None:
        report = SourceReport(status="complete")  # type: ignore[arg-type]
        result = MarketResearchAgent(StaticSource(report)).run(make_context())

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertIn("INVALID_SOURCE_REPORT", {error.code for error in result.errors})

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
            errors=(AgentError("SOURCE_UNAVAILABLE", "A source could not be reached."),),
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

    def test_market_research_context_has_no_new_product_result_input(self) -> None:
        source = StaticSource(SourceReport(status=ResultStatus.NO_RESULTS))
        context = make_context()
        result = MarketResearchAgent(source).run(context)

        self.assertIs(source.received_context, context)
        self.assertFalse(hasattr(source.received_context, "product_concept"))
        self.assertEqual(result.request_id, "request-123")
        self.assertEqual(result.status, ResultStatus.NO_RESULTS)

    def test_upstream_partial_status_and_limitations_are_preserved(self) -> None:
        report = SourceReport(
            status=ResultStatus.OK,
            findings=(
                ResearchFinding(
                    finding_id="finding-1",
                    topic="market context",
                    statement="A source-backed observation.",
                    kind=FindingKind.FACT,
                    evidence_ids=("market:E-001",),
                ),
            ),
            evidence=(make_evidence(),),
        )
        context = MarketResearchContext(
            request_id="request-123",
            planning_request={"query": "spring knitwear"},
            trend_result={
                "result": {
                    "status": "partial",
                    "limitations": ["Some trend sources were unavailable."],
                    "errors": [
                        {"code": "SOURCE_TIMEOUT", "message": "sensitive source detail"}
                    ],
                },
                "missing_fields": ["market"],
                "evidence_ids": ["trend:E-001"],
            },
            inventory_product_result={"status": "ok"},
        )
        source = StaticSource(report)

        result = MarketResearchAgent(source).run(context)

        self.assertIs(source.received_context, context)
        self.assertEqual(
            source.received_context.trend_result["evidence_ids"], ["trend:E-001"]
        )
        self.assertEqual(result.status, ResultStatus.PARTIAL)
        self.assertIn(
            "Upstream input 'trend_result' reported status 'partial'.",
            result.limitations,
        )
        self.assertIn(
            "Upstream trend_result: Some trend sources were unavailable.",
            result.limitations,
        )
        self.assertIn(
            "Upstream trend_result is missing field 'market'.",
            result.limitations,
        )
        self.assertIn(
            "Upstream trend_result reported error code 'SOURCE_TIMEOUT'.",
            result.limitations,
        )
        self.assertFalse(any("sensitive source detail" in item for item in result.limitations))

    def test_absent_optional_upstream_input_does_not_block_research(self) -> None:
        source = StaticSource(SourceReport(status=ResultStatus.NO_RESULTS))
        context = MarketResearchContext(
            request_id="request-123", planning_request={"query": "spring knitwear"}
        )

        result = MarketResearchAgent(source).run(context)

        self.assertIs(source.received_context, context)
        self.assertEqual(result.status, ResultStatus.NO_RESULTS)
        self.assertTrue(
            any("trend_result' was not supplied" in item for item in result.limitations)
        )

    def test_source_error_keeps_upstream_context_limitations(self) -> None:
        context = MarketResearchContext(
            request_id="request-123",
            planning_request={"query": "spring knitwear"},
            trend_result={
                "status": "partial",
                "limitations": ["Trend coverage is incomplete."],
            },
            inventory_product_result={"status": "ok"},
        )

        result = MarketResearchAgent(
            StaticSource(error=TimeoutError("sensitive details"))
        ).run(context)

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.errors[0].code, "TIMEOUT")
        self.assertIn(
            "Upstream trend_result: Trend coverage is incomplete.",
            result.limitations,
        )

    def test_request_id_mismatch_stops_combining_upstream_context(self) -> None:
        source = StaticSource(SourceReport(status=ResultStatus.NO_RESULTS))
        context = MarketResearchContext(
            request_id="request-123",
            planning_request={"query": "spring knitwear"},
            trend_result={"request_id": "request-other", "status": "ok"},
        )

        result = MarketResearchAgent(source).run(context)

        self.assertIsNone(source.received_context)
        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.errors[0].code, "UPSTREAM_REQUEST_ID_MISMATCH")

    def test_empty_request_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MarketResearchAgent().run(
                MarketResearchContext(request_id=" ", planning_request={})
            )


if __name__ == "__main__":
    unittest.main()
