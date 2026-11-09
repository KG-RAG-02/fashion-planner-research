"""Combine reports from explicitly configured, authorized source adapters."""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.models import (
    AgentError,
    MarketResearchContext,
    ResultStatus,
    SourceReport,
)
from ..domain.sales import MarketDataResult, SalesDataResult, UnitEconomicsResult
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
        sales_reports = [report.sales_data for report in reports if report.sales_data is not None]
        sales_data = None
        if sales_reports:
            observations = tuple(item for sales in sales_reports for item in sales.observations)
            sales_statuses = {sales.status for sales in sales_reports}
            sales_status = _domain_status(sales_statuses, bool(observations))
            sales_data = SalesDataResult(
                status=sales_status,
                observations=observations,
                limitations=tuple(dict.fromkeys(item for sales in sales_reports for item in sales.limitations)),
                errors=tuple(item for sales in sales_reports for item in sales.errors),
                follow_up_questions=tuple(item for sales in sales_reports for item in sales.follow_up_questions),
                missing_data=tuple(item for sales in sales_reports for item in sales.missing_data),
            )

        market_reports = [report.market_data for report in reports if report.market_data is not None]
        market_data = None
        if market_reports:
            observations = tuple(item for market in market_reports for item in market.observations)
            domain_statuses = {market.status for market in market_reports}
            domain_status = _domain_status(domain_statuses, bool(observations))
            market_data = MarketDataResult(
                status=domain_status,
                observations=observations,
                limitations=tuple(dict.fromkeys(item for market in market_reports for item in market.limitations)),
                errors=tuple(item for market in market_reports for item in market.errors),
                missing_data=tuple(item for market in market_reports for item in market.missing_data),
            )

        economics_reports = [report.unit_economics for report in reports if report.unit_economics is not None]
        unit_economics = None
        if economics_reports:
            calculations = tuple(item for economics in economics_reports for item in economics.calculations)
            domain_statuses = {economics.status for economics in economics_reports}
            unit_economics = UnitEconomicsResult(
                status=_domain_status(domain_statuses, bool(calculations)),
                calculations=calculations,
                limitations=tuple(dict.fromkeys(item for economics in economics_reports for item in economics.limitations)),
                errors=tuple(item for economics in economics_reports for item in economics.errors),
                missing_data=tuple(item for economics in economics_reports for item in economics.missing_data),
            )

        has_sales_observations = bool(sales_data and sales_data.observations)
        has_source_content = bool(
            findings or evidence or has_sales_observations
            or (market_data and market_data.observations)
            or (unit_economics and unit_economics.calculations)
        )
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
        elif has_source_content:
            successful = any(report.status == ResultStatus.OK for report in reports)
            missing = any(report.status == ResultStatus.NO_RESULTS for report in reports)
            status = ResultStatus.PARTIAL if successful and missing else ResultStatus.OK
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
            sales_data=sales_data,
            market_data=market_data,
            unit_economics=unit_economics,
        )


def _domain_status(statuses: set[str], has_observations: bool) -> str:
    if "partial" in statuses or ("ok" in statuses and "no_results" in statuses):
        return "partial"
    if "error" in statuses:
        return "partial" if has_observations else "error"
    if has_observations:
        return "ok"
    return "no_results"
