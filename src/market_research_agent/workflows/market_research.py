"""Use case that checks source evidence and builds a market result."""

from __future__ import annotations

from ..domain.models import (
    AgentError,
    FindingKind,
    MarketResearchContext,
    MarketResearchResult,
    PermissionStatus,
    ResultStatus,
    SourceReport,
)
from ..sources.noop import NoConfiguredSources
from ..sources.protocol import MarketResearchSource


class MarketResearchAgent:
    """Build a result while enforcing source permission and evidence links."""

    agent_id = "market-research-agent"
    schema_version = "1.0"

    def __init__(self, source: MarketResearchSource | None = None) -> None:
        self._source = source or NoConfiguredSources()

    def run(self, context: MarketResearchContext) -> MarketResearchResult:
        if not context.request_id.strip():
            raise ValueError("request_id must not be empty")

        try:
            report = self._source.research(context)
        except PermissionError:
            return self._failure(
                context.request_id,
                AgentError(
                    code="FORBIDDEN",
                    message="The configured source denied access.",
                ),
            )
        except TimeoutError:
            return self._failure(
                context.request_id,
                AgentError(
                    code="TIMEOUT",
                    message="The configured source did not respond in time.",
                ),
            )
        except Exception:
            # Avoid returning exception strings that may contain credentials or source data.
            return self._failure(
                context.request_id,
                AgentError(
                    code="SOURCE_UNAVAILABLE",
                    message="The configured source could not be queried.",
                ),
            )

        if not isinstance(report, SourceReport):
            return self._failure(
                context.request_id,
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="The source adapter returned an unsupported report type.",
                ),
            )
        if not isinstance(report.status, ResultStatus):
            return self._failure(
                context.request_id,
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="The source adapter returned an unsupported result status.",
                ),
            )

        return self._assemble(context.request_id, report)

    def _assemble(
        self, request_id: str, report: SourceReport
    ) -> MarketResearchResult:
        errors = list(report.errors)
        limitations = list(report.limitations)

        all_evidence = {}
        duplicate_ids: set[str] = set()
        for evidence in report.evidence:
            if not self._has_required_identity(
                evidence.evidence_id,
                evidence.source_id,
                evidence.source_type,
                evidence.source_name,
            ):
                errors.append(
                    AgentError(
                        code="INVALID_EVIDENCE",
                        message="Evidence without complete source identity was omitted.",
                    )
                )
                continue
            if evidence.evidence_id in all_evidence:
                duplicate_ids.add(evidence.evidence_id)
            all_evidence[evidence.evidence_id] = evidence

        if duplicate_ids:
            errors.append(
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="Duplicate evidence identifiers were omitted.",
                )
            )

        permitted_evidence = {
            evidence_id: evidence
            for evidence_id, evidence in all_evidence.items()
            if evidence.permission_status == PermissionStatus.AUTHORIZED
            and evidence_id not in duplicate_ids
        }
        unapproved_ids = set(all_evidence) - set(permitted_evidence)
        if unapproved_ids:
            limitations.append(
                "Evidence with denied or unconfirmed usage permission was omitted."
            )
            errors.append(
                AgentError(
                    code="EVIDENCE_PERMISSION_UNCONFIRMED",
                    message="Some source evidence was not confirmed for use.",
                )
            )

        findings = []
        seen_finding_ids: set[str] = set()
        all_ids = set(all_evidence)
        for finding in report.findings:
            problem = not self._has_required_identity(
                finding.finding_id, finding.topic, finding.statement
            )
            duplicate_finding = finding.finding_id in seen_finding_ids
            seen_finding_ids.add(finding.finding_id)

            if duplicate_finding:
                problem = True
            if not isinstance(finding.kind, FindingKind):
                problem = True
            elif finding.kind == FindingKind.FACT and not finding.evidence_ids:
                problem = True
            if not set(finding.evidence_ids).issubset(all_ids):
                problem = True
            if not set(finding.evidence_ids).issubset(permitted_evidence):
                problem = True

            if problem:
                errors.append(
                    AgentError(
                        code="UNSUPPORTED_FINDING",
                        message="A finding with invalid or unavailable evidence was omitted.",
                    )
                )
                continue
            findings.append(finding)

        evidence = tuple(permitted_evidence.values())
        findings_tuple = tuple(findings)
        has_content = bool(findings_tuple or evidence)
        if report.status == ResultStatus.NO_RESULTS and has_content:
            errors.append(
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="A no_results report contained findings or evidence.",
                )
            )
        has_issues = bool(errors)

        if report.status == ResultStatus.ERROR:
            status = ResultStatus.PARTIAL if has_content else ResultStatus.ERROR
        elif has_issues:
            status = ResultStatus.PARTIAL if has_content else ResultStatus.ERROR
        elif report.status == ResultStatus.PARTIAL:
            status = ResultStatus.PARTIAL
        elif report.status == ResultStatus.NO_RESULTS or not has_content:
            status = ResultStatus.NO_RESULTS
        else:
            status = ResultStatus.OK

        return MarketResearchResult(
            schema_version=self.schema_version,
            request_id=request_id,
            agent_id=self.agent_id,
            status=status,
            summary=self._summary(status, len(findings_tuple), len(evidence)),
            findings=findings_tuple,
            evidence=evidence,
            limitations=tuple(dict.fromkeys(limitations)),
            errors=tuple(errors),
        )

    def _failure(self, request_id: str, error: AgentError) -> MarketResearchResult:
        return MarketResearchResult(
            schema_version=self.schema_version,
            request_id=request_id,
            agent_id=self.agent_id,
            status=ResultStatus.ERROR,
            summary="Market research did not produce a usable result.",
            limitations=(),
            errors=(error,),
        )

    @staticmethod
    def _summary(status: ResultStatus, finding_count: int, evidence_count: int) -> str:
        if status == ResultStatus.OK:
            return (
                f"Produced {finding_count} findings supported by "
                f"{evidence_count} evidence records."
            )
        if status == ResultStatus.PARTIAL:
            return (
                f"Produced {finding_count} findings with partial source coverage; "
                f"{evidence_count} authorized evidence records were retained."
            )
        if status == ResultStatus.NO_RESULTS:
            return "No market findings were returned."
        return "Market research did not produce a usable result."

    @staticmethod
    def _has_required_identity(*values: str) -> bool:
        """Reject blank identifiers and labels before they enter the shared result."""

        return all(isinstance(value, str) and value.strip() for value in values)
