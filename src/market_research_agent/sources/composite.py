"""Combine reports from explicitly configured, authorized source adapters."""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.models import (
    AgentError,
    MarketResearchContext,
    ResultStatus,
    SourceReport,
)
from .protocol import MarketResearchSource


class CompositeMarketResearchSource:
    """Query configured sources and retain usable results when one source fails.

    Sources are called sequentially to avoid choosing an unapproved concurrency,
    timeout, or retry policy. This class does not select or configure providers;
    callers must supply only sources cleared for the intended data and use.
    """

    def __init__(self, sources: Iterable[MarketResearchSource] = ()) -> None:
        self._sources = tuple(sources)

    def research(self, context: MarketResearchContext) -> SourceReport:
        if not self._sources:
            return SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=("No authorized market research source is configured.",),
            )

        reports: list[SourceReport] = []
        errors: list[AgentError] = []
        for source in self._sources:
            try:
                report = source.research(context)
            except PermissionError:
                errors.append(
                    AgentError("FORBIDDEN", "A configured source denied access.")
                )
                continue
            except TimeoutError:
                errors.append(
                    AgentError("TIMEOUT", "A configured source did not respond in time.")
                )
                continue
            except Exception:
                # Exception text can contain credentials or private source data.
                errors.append(
                    AgentError(
                        "SOURCE_UNAVAILABLE",
                        "A configured source could not be queried.",
                    )
                )
                continue

            if not isinstance(report, SourceReport) or not isinstance(
                report.status, ResultStatus
            ):
                errors.append(
                    AgentError(
                        "INVALID_SOURCE_REPORT",
                        "A configured source returned an unsupported report.",
                    )
                )
                continue

            if report.status == ResultStatus.NO_RESULTS and (
                report.findings or report.evidence
            ):
                errors.append(
                    AgentError(
                        "INVALID_SOURCE_REPORT",
                        "A no_results source report contained findings or evidence.",
                    )
                )
                reports.append(
                    SourceReport(
                        status=ResultStatus.ERROR,
                        limitations=report.limitations,
                        errors=report.errors,
                    )
                )
                continue

            reports.append(report)
            if report.status == ResultStatus.ERROR and not report.errors:
                errors.append(
                    AgentError(
                        "SOURCE_UNAVAILABLE",
                        "A configured source returned an error without details.",
                    )
                )

        findings = tuple(item for report in reports for item in report.findings)
        evidence = tuple(item for report in reports for item in report.evidence)
        limitations = tuple(
            item for report in reports for item in report.limitations
        )
        errors.extend(error for report in reports for error in report.errors)

        if not reports:
            status = ResultStatus.ERROR
        elif any(report.status == ResultStatus.PARTIAL for report in reports):
            status = ResultStatus.PARTIAL
        elif any(report.status == ResultStatus.ERROR for report in reports):
            has_recoverable_source = any(
                report.status in {ResultStatus.OK, ResultStatus.NO_RESULTS}
                for report in reports
            )
            if findings or evidence or has_recoverable_source:
                status = ResultStatus.PARTIAL
            else:
                status = ResultStatus.ERROR
        elif errors:
            status = ResultStatus.PARTIAL
        elif findings or evidence:
            status = ResultStatus.OK
        elif all(report.status == ResultStatus.NO_RESULTS for report in reports):
            status = ResultStatus.NO_RESULTS
        else:
            status = ResultStatus.NO_RESULTS

        return SourceReport(
            status=status,
            findings=findings,
            evidence=evidence,
            limitations=tuple(dict.fromkeys(limitations)),
            errors=tuple(errors),
        )
