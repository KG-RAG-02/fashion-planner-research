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
from ..sources.shared.noop import NoConfiguredSources
from ..sources.shared.protocol import MarketResearchSource
from ..domain.sales import MarketDataResult, UnitEconomicsResult


class MarketResearchAgent:
    """Build a result while enforcing source permission and evidence links."""

    agent_id = "market-research-agent"
    # This is the implemented local envelope version. Proposed fields in the
    # schema draft are not advertised as a shared wire-contract version.
    schema_version = "1.0"

    def __init__(self, source: MarketResearchSource | None = None) -> None:
        self._source = source or NoConfiguredSources()

    def run(self, context: MarketResearchContext) -> MarketResearchResult:
        if not context.request_id.strip():
            raise ValueError("request_id must not be empty")

        context_limitations, incomplete_context = self._context_limitations(context)
        mismatched_input = self._request_id_mismatch(context)
        if mismatched_input:
            return self._failure(
                context.request_id,
                AgentError(
                    code="UPSTREAM_REQUEST_ID_MISMATCH",
                    message=(
                        f"Upstream input '{mismatched_input}' belongs to a different request."
                    ),
                ),
                limitations=context_limitations,
            )

        try:
            report = self._source.research(context)
        except PermissionError:
            return self._failure(
                context.request_id,
                AgentError(
                    code="FORBIDDEN",
                    message="The configured source denied access.",
                ),
                limitations=context_limitations,
            )
        except TimeoutError:
            return self._failure(
                context.request_id,
                AgentError(
                    code="TIMEOUT",
                    message="The configured source did not respond in time.",
                ),
                limitations=context_limitations,
            )
        except Exception:
            # Avoid returning exception strings that may contain credentials or source data.
            return self._failure(
                context.request_id,
                AgentError(
                    code="SOURCE_UNAVAILABLE",
                    message="The configured source could not be queried.",
                ),
                limitations=context_limitations,
            )

        if not isinstance(report, SourceReport):
            return self._failure(
                context.request_id,
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="The source adapter returned an unsupported report type.",
                ),
                limitations=context_limitations,
            )
        if not isinstance(report.status, ResultStatus):
            return self._failure(
                context.request_id,
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="The source adapter returned an unsupported result status.",
                ),
                limitations=context_limitations,
            )

        return self._assemble(
            context.request_id,
            report,
            context_limitations=context_limitations,
            incomplete_context=incomplete_context,
            requested_scope={
                "category": context.requested_category,
                "metrics": list(context.requested_metrics),
                "geography": context.requested_geography,
                "channel": context.requested_channel,
                "query": context.requested_query,
            },
        )

    def _assemble(
        self,
        request_id: str,
        report: SourceReport,
        *,
        context_limitations: tuple[str, ...] = (),
        incomplete_context: bool = False,
        requested_scope: dict | None = None,
    ) -> MarketResearchResult:
        errors = list(report.errors)
        limitations = [*report.limitations, *context_limitations]

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
        sales_data = report.sales_data
        if sales_data is not None:
            valid_sales = tuple(
                item for item in sales_data.observations
                if set(item.evidence_ids).issubset(permitted_evidence)
            )
            if len(valid_sales) != len(sales_data.observations):
                limitations.append(
                    "Sales observations without authorized, resolvable Evidence were omitted."
                )
                errors.append(
                    AgentError(
                        code="INVALID_SALES_EVIDENCE",
                        message="Some sales observations did not reference authorized Evidence.",
                    )
                )
            sales_data = type(sales_data)(
                status=("partial" if sales_data.status == "ok" and len(valid_sales) != len(sales_data.observations) else sales_data.status),
                observations=valid_sales,
                limitations=sales_data.limitations,
                errors=sales_data.errors,
                follow_up_questions=sales_data.follow_up_questions,
                missing_data=sales_data.missing_data,
            )
        market_data = report.market_data
        if market_data is not None:
            valid_market = tuple(
                item for item in market_data.observations
                if set(item.evidence_ids).issubset(permitted_evidence)
            )
            if len(valid_market) != len(market_data.observations):
                limitations.append("Market observations without authorized, resolvable Evidence were omitted.")
                errors.append(AgentError("INVALID_MARKET_EVIDENCE", "Some market observations did not reference authorized Evidence."))
            market_data = MarketDataResult(
                status=("partial" if market_data.status == "ok" and len(valid_market) != len(market_data.observations) else market_data.status),
                observations=valid_market,
                limitations=market_data.limitations,
                errors=market_data.errors,
                missing_data=market_data.missing_data,
            )
        unit_economics = report.unit_economics
        if unit_economics is not None:
            valid_calculations = tuple(
                item for item in unit_economics.calculations
                if set(item.evidence_ids).issubset(permitted_evidence)
                and set(item.input_evidence_ids).issubset(permitted_evidence)
            )
            if len(valid_calculations) != len(unit_economics.calculations):
                limitations.append("Unit-economics calculations without authorized, resolvable input and result Evidence were omitted.")
                errors.append(AgentError("INVALID_UNIT_ECONOMICS_EVIDENCE", "Some calculations did not reference authorized input and result Evidence."))
            unit_economics = UnitEconomicsResult(
                status=("partial" if unit_economics.status == "ok" and len(valid_calculations) != len(unit_economics.calculations) else unit_economics.status),
                calculations=valid_calculations,
                limitations=unit_economics.limitations,
                errors=unit_economics.errors,
                missing_data=unit_economics.missing_data,
            )
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
        has_content = bool(
            findings_tuple
            or evidence
            or (sales_data is not None and sales_data.observations)
            or (market_data is not None and market_data.observations)
            or (unit_economics is not None and unit_economics.calculations)
        )
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
        elif report.status == ResultStatus.PARTIAL:
            status = ResultStatus.PARTIAL
        elif has_issues:
            status = ResultStatus.PARTIAL if has_content else ResultStatus.ERROR
        elif report.status == ResultStatus.NO_RESULTS or not has_content:
            status = ResultStatus.NO_RESULTS
        elif incomplete_context:
            status = ResultStatus.PARTIAL
        else:
            status = ResultStatus.OK

        requested_metrics = {item.casefold() for item in (requested_scope or {}).get("metrics", [])}
        expected_domains = set()
        if requested_metrics & {"sales", "sales_units", "sales_revenue", "units_sold"}:
            expected_domains.add("sales")
        if requested_metrics & {"market_size", "retail_sales_index", "market_share", "trend_interest"}:
            expected_domains.add("market")
        if requested_metrics & {"unit_margin", "cost_margin", "margin", "gross_margin"}:
            expected_domains.add("unit_economics")
        missing_expected = (
            ("sales" in expected_domains and (sales_data is None or sales_data.status == "no_results"))
            or ("market" in expected_domains and (market_data is None or market_data.status == "no_results"))
            or ("unit_economics" in expected_domains and (unit_economics is None or unit_economics.status == "no_results"))
        )
        if missing_expected and status in {ResultStatus.OK, ResultStatus.NO_RESULTS}:
            status = ResultStatus.PARTIAL if has_content else ResultStatus.NO_RESULTS
            limitations.append("One or more requested data domains returned no usable observations or calculations.")

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
            data_limitations=report.data_limitations,
            sales_data=sales_data,
            market_data=market_data,
            unit_economics=unit_economics,
            requested_scope=requested_scope,
        )

    def _failure(
        self,
        request_id: str,
        error: AgentError,
        *,
        limitations: tuple[str, ...] = (),
    ) -> MarketResearchResult:
        return MarketResearchResult(
            schema_version=self.schema_version,
            request_id=request_id,
            agent_id=self.agent_id,
            status=ResultStatus.ERROR,
            summary="Market research did not produce a usable result.",
            limitations=limitations,
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

    @staticmethod
    def _context_limitations(
        context: MarketResearchContext,
    ) -> tuple[tuple[str, ...], bool]:
        """Summarize upstream availability without requiring an unapproved schema.

        The original common context is still passed unchanged to the source
        adapter. This reader only carries common status/limitation metadata into
        the result; it never reads another agent's output.
        """

        notes: list[str] = []
        incomplete = False
        for name in ("planning_request", "trend_result", "inventory_product_result"):
            payload = getattr(context, name)
            if payload is None:
                if name != "planning_request":
                    notes.append(f"Optional upstream input '{name}' was not supplied.")
                    incomplete = True
                continue
            if not isinstance(payload, dict):
                notes.append(f"Upstream input '{name}' was not a readable object.")
                incomplete = True
                continue

            nested_result = payload.get("result")
            metadata = nested_result if isinstance(nested_result, dict) else payload
            status = metadata.get("status", payload.get("status"))
            if isinstance(status, str) and status in {"partial", "no_results", "error"}:
                notes.append(f"Upstream input '{name}' reported status '{status}'.")
                incomplete = True
            elif status is not None and status != "ok":
                notes.append(f"Upstream input '{name}' reported an unrecognized status.")
                incomplete = True

            for source in (payload, metadata):
                for limitation in MarketResearchAgent._string_items(
                    source.get("limitations")
                ):
                    if isinstance(limitation, str) and limitation.strip():
                        notes.append(f"Upstream {name}: {limitation.strip()}")
                for missing in MarketResearchAgent._string_items(
                    source.get("missing_fields")
                ):
                    if isinstance(missing, str) and missing.strip():
                        notes.append(
                            f"Upstream {name} is missing field '{missing.strip()}'."
                        )
                for error in MarketResearchAgent._mapping_items(source.get("errors")):
                    code = error.get("code")
                    if isinstance(code, str) and code.strip():
                        notes.append(
                            f"Upstream {name} reported error code '{code.strip()}'."
                        )
                        incomplete = True

        return tuple(dict.fromkeys(notes)), incomplete

    @staticmethod
    def _request_id_mismatch(context: MarketResearchContext) -> str | None:
        """Reject conflicting common-envelope request IDs when a caller provides them."""

        for name in ("planning_request", "trend_result", "inventory_product_result"):
            payload = getattr(context, name)
            if not isinstance(payload, dict):
                continue
            nested_result = payload.get("result")
            metadata = nested_result if isinstance(nested_result, dict) else payload
            request_id = metadata.get("request_id", payload.get("request_id"))
            if request_id is not None and request_id != context.request_id:
                return name
        return None

    @staticmethod
    def _string_items(value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(item for item in value if isinstance(item, str))
        return ()

    @staticmethod
    def _mapping_items(value: object) -> tuple[dict, ...]:
        if isinstance(value, dict):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(item for item in value if isinstance(item, dict))
        return ()
