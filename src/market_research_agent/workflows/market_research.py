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
from ..domain.sales import ComparabilityResult, MarketDataResult, MissingData, UnitEconomicsResult, compare_sales_to_market
from ..sources.market.analysis import calculate_market_series_change
from ..sources.market.evidence_retrieval import MarketEvidenceRetriever, RetrievalQuery, RetrievedEvidence
from ..domain.models import EvidenceRecord
import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    domain: str
    status: str
    hits: tuple[RetrievedEvidence, ...] = ()
    missing_data: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "status": self.status,
            "hits": [
                {"chunk": asdict(hit.chunk) | {"permission_status": hit.chunk.permission_status.value}, "score": hit.score}
                for hit in self.hits
            ],
            "missing_data": list(self.missing_data),
        }


class MarketResearchAgent:
    """Build a result while enforcing source permission and evidence links."""

    agent_id = "sales-market-agent"
    # This is the implemented local envelope version. Proposed fields in the
    # schema draft are not advertised as a shared wire-contract version.
    schema_version = "1.0"

    def __init__(self, source: MarketResearchSource | None = None, *, sales_retriever: MarketEvidenceRetriever | None = None, market_retriever: MarketEvidenceRetriever | None = None) -> None:
        self._source = source or NoConfiguredSources()
        self._sales_retriever = sales_retriever
        self._market_retriever = market_retriever

    def run(self, context: MarketResearchContext) -> MarketResearchResult:
        if not isinstance(context.request_id, str) or not context.request_id.strip():
            raise ValueError("request_id must not be empty")

        context_limitations, incomplete_context = self._context_limitations(context)
        invalid_upstream = self._validate_upstream_context(context)
        if invalid_upstream:
            error_code, invalid_input = invalid_upstream
            return self._failure(
                context.request_id,
                AgentError(
                    code=error_code,
                    message=f"Upstream input '{invalid_input}' did not satisfy the local safety envelope.",
                ),
                limitations=context_limitations,
            )
        upstream_context = self._snapshot_upstream_context(context)
        requested_scope = self._requested_scope(context)
        if context.canonical_request is not None and context.query_ambiguities:
            ambiguity_report = SourceReport(
                status=ResultStatus.NO_RESULTS,
                limitations=(
                    "Canonical request contains unresolved ambiguity; no sales or market source was queried.",
                ),
            )
            return self._assemble(
                context.request_id, ambiguity_report,
                context_limitations=context_limitations,
                incomplete_context=True,
                requested_scope=requested_scope,
                upstream_context=upstream_context,
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
                upstream_context=upstream_context,
            )
        except TimeoutError:
            return self._failure(
                context.request_id,
                AgentError(
                    code="TIMEOUT",
                    message="The configured source did not respond in time.",
                ),
                limitations=context_limitations,
                upstream_context=upstream_context,
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
                upstream_context=upstream_context,
            )

        if not isinstance(report, SourceReport):
            return self._failure(
                context.request_id,
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="The source adapter returned an unsupported report type.",
                ),
                limitations=context_limitations,
                upstream_context=upstream_context,
            )
        if not isinstance(report.status, ResultStatus):
            return self._failure(
                context.request_id,
                AgentError(
                    code="INVALID_SOURCE_REPORT",
                    message="The source adapter returned an unsupported result status.",
                ),
                limitations=context_limitations,
                upstream_context=upstream_context,
            )

        retrieval_evidence: list[EvidenceRecord] = []
        retrieval_errors: list[AgentError] = []
        retrieval_limitations: list[str] = []
        retrieval_results = {}
        if context.requested_query and context.requested_query.strip():
            for domain, retriever in (("sales", self._sales_retriever), ("market", self._market_retriever)):
                hits: tuple[RetrievedEvidence, ...] = ()
                missing = ()
                status = "no_results"
                if retriever is None:
                    missing = ("retriever_not_configured",)
                else:
                    try:
                        raw_hits = tuple(retriever.retrieve(RetrievalQuery(
                            raw_query=context.requested_query,
                            category=context.requested_category,
                            geography=context.requested_geography,
                            channel=context.requested_channel,
                            metrics=context.requested_metrics,
                            as_of_or_period=context.requested_period,
                            population=context.requested_population,
                            unit=context.requested_unit,
                            currency=context.requested_currency,
                            measure_type=context.requested_measure_type,
                            mapping_id=context.requested_mapping_id,
                            mapping_version=context.requested_mapping_version,
                            period_grain=context.requested_period_grain,
                            document_version=context.requested_document_version,
                        )))
                        valid_hits = []
                        invalid_hit_count = 0
                        for hit in raw_hits:
                            if (isinstance(hit, RetrievedEvidence)
                                    and hit.chunk.permission_status == PermissionStatus.AUTHORIZED
                                    and math.isfinite(hit.score)):
                                valid_hits.append(hit)
                            else:
                                invalid_hit_count += 1
                        hits = tuple(valid_hits)
                        status = "ok" if hits else "no_results"
                        if invalid_hit_count:
                            missing = ("unauthorized_or_invalid_hits_omitted",)
                            if hits:
                                status = "partial"
                            else:
                                status = "error"
                                retrieval_errors.append(AgentError(
                                    "RETRIEVAL_INVALID_HITS",
                                    f"The {domain} evidence retriever returned no usable authorized evidence.",
                                ))
                    except PermissionError:
                        missing = ("access_denied",)
                        status = "error"
                        retrieval_errors.append(AgentError("RETRIEVAL_FORBIDDEN", f"The {domain} evidence retriever denied access."))
                    except Exception:
                        missing = ("retrieval_error",)
                        status = "error"
                        retrieval_errors.append(AgentError("RETRIEVAL_UNAVAILABLE", f"The {domain} evidence retriever could not be queried."))
                for hit in hits:
                    chunk = hit.chunk
                    retrieval_evidence.append(EvidenceRecord(
                        evidence_id=chunk.evidence_id, source_id=chunk.source_id,
                        source_type=f"{domain}_explanatory_document", source_name=chunk.source_name,
                        locator=chunk.locator, published_at=chunk.published_at,
                        observed_at=chunk.observed_at, fetched_at=chunk.fetched_at,
                        geography=chunk.geography, channel=chunk.channel,
                        product_mapping=chunk.mapping_version or chunk.category,
                        permission_status=PermissionStatus.AUTHORIZED,
                    ))
                retrieval_results[domain] = RetrievalResult(domain, status, hits, missing)
        if report.status == ResultStatus.NO_RESULTS and retrieval_evidence:
            retrieval_limitations.append("Authorized explanatory evidence was retrieved, but no structured observations or factual synthesis were produced.")
        report = SourceReport(
            status=ResultStatus.PARTIAL if report.status == ResultStatus.NO_RESULTS and retrieval_evidence else report.status,
            findings=report.findings,
            evidence=(*report.evidence, *retrieval_evidence), limitations=(*report.limitations, *retrieval_limitations),
            errors=(*report.errors, *retrieval_errors), data_limitations=report.data_limitations,
            sales_data=report.sales_data, market_data=report.market_data,
            unit_economics=report.unit_economics,
        )

        return self._assemble(
            context.request_id,
            report,
            context_limitations=context_limitations,
            incomplete_context=incomplete_context,
            requested_scope=requested_scope,
            sales_retrieval=retrieval_results.get("sales"),
            market_retrieval=retrieval_results.get("market"),
            upstream_context=upstream_context,
        )

    def _assemble(
        self,
        request_id: str,
        report: SourceReport,
        *,
        context_limitations: tuple[str, ...] = (),
        incomplete_context: bool = False,
        requested_scope: dict | None = None,
        sales_retrieval=None,
        market_retrieval=None,
        upstream_context=None,
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
                calculations=sales_data.calculations,
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
                calculations=market_data.calculations,
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
        def keep_resolvable_hits(retrieval):
            if retrieval is None:
                return None
            kept = tuple(hit for hit in retrieval.hits if hit.chunk.evidence_id in permitted_evidence)
            removed = len(kept) != len(retrieval.hits)
            return RetrievalResult(
                retrieval.domain,
                "partial" if removed and kept else "error" if removed else retrieval.status,
                kept,
                (*retrieval.missing_data, *(("evidence_identity_conflict",) if removed else ())),
            )
        sales_retrieval = keep_resolvable_hits(sales_retrieval)
        market_retrieval = keep_resolvable_hits(market_retrieval)
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
        if requested_metrics & {"sales", "category_sales", "purchase_rows", "sales_units", "sales_revenue", "units_sold"}:
            expected_domains.add("sales")
        if requested_metrics & {"market_size", "market_size_growth", "market_growth", "retail_sales_index", "retail_sales_index_change", "market_share", "trend_interest"}:
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

        comparisons = []
        comparison_truncated = False
        if sales_data and market_data:
            for sales_item in sales_data.observations:
                for market_item in market_data.observations:
                    if sales_item.category and market_item.category and sales_item.category.casefold() == market_item.category.casefold():
                        if len(comparisons) == 5000:
                            comparison_truncated = True
                            break
                        comparisons.append(compare_sales_to_market(
                            sales_item, market_item,
                            sales_permission_authorized=True,
                            market_permission_authorized=True,
                        ))
                if comparison_truncated:
                    break
        if comparison_truncated:
            comparisons.append(ComparabilityResult(
                False, mismatches=("comparison_matrix:limited_to_5000_pairs",),
            ))
            limitations.append("Comparability matrix was limited to 5,000 observation pairs; additional pairs were not evaluated.")
            if status == ResultStatus.OK:
                status = ResultStatus.PARTIAL
        requested_metrics = {str(item).casefold() for item in (requested_scope or {}).get("metrics", [])}
        if market_data and requested_metrics & {"market_size_growth", "retail_sales_index_change", "market_growth"}:
            groups: dict[tuple, list] = {}
            for item in market_data.observations:
                key = (item.metric_name, item.measure_type, item.unit, item.currency, item.geography,
                       item.channel, item.category, item.population, item.product_mapping_id,
                       item.product_mapping_version, item.period_grain, item.metric_definition)
                groups.setdefault(key, []).append(item)
            calculations = list(market_data.calculations)
            calculation_issues = []
            for series in groups.values():
                series.sort(key=lambda item: (item.period_start or "", item.period_end or "", item.observation_id))
                for baseline, current in zip(series, series[1:]):
                    try:
                        calculations.extend(calculate_market_series_change(baseline, current))
                    except (ValueError, TypeError):
                        calculation_issues.append(f"Series {baseline.observation_id} → {current.observation_id} was not comparable for the requested calculation.")
            calculation_missing = (MissingData(
                "market_data.calculations", "not_available_from_source",
                "One or more requested calculations were blocked by incompatible or incomplete series scope.",
            ),) if calculation_issues else ()
            market_data = MarketDataResult(
                status="partial" if calculation_issues and market_data.status == "ok" else market_data.status,
                observations=market_data.observations,
                limitations=(*market_data.limitations, *calculation_issues), errors=market_data.errors,
                missing_data=(*market_data.missing_data, *calculation_missing),
                calculations=tuple(calculations),
            )
            if calculation_issues:
                limitations.append("One or more requested market calculations were blocked by incompatible or incomplete series scope.")
                if status == ResultStatus.OK:
                    status = ResultStatus.PARTIAL
        domain_statuses = [
            item.status for item in (sales_data, market_data, unit_economics) if item is not None
        ]
        if any(item == "error" for item in domain_statuses):
            status = ResultStatus.PARTIAL if has_content else ResultStatus.ERROR
        elif any(item == "partial" for item in domain_statuses) and has_content and status == ResultStatus.OK:
            status = ResultStatus.PARTIAL
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
            sales_retrieval=sales_retrieval,
            market_retrieval=market_retrieval,
            comparability=tuple(comparisons),
            upstream_context=upstream_context,
        )

    def _failure(
        self,
        request_id: str,
        error: AgentError,
        *,
        limitations: tuple[str, ...] = (),
        upstream_context: dict | None = None,
    ) -> MarketResearchResult:
        return MarketResearchResult(
            schema_version=self.schema_version,
            request_id=request_id,
            agent_id=self.agent_id,
            status=ResultStatus.ERROR,
            summary="Market research did not produce a usable result.",
            limitations=limitations,
            errors=(error,),
            upstream_context=upstream_context,
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
                if name != "planning_request" and context.canonical_request is None:
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
    def _requested_scope(context: MarketResearchContext) -> dict:
        gender = (context.explicit_scope or {}).get("gender")
        if gender is None and context.inferred_scope:
            inferred_gender = context.inferred_scope.get("gender")
            gender = inferred_gender.get("value") if isinstance(inferred_gender, dict) else inferred_gender
        return {
            "category": context.requested_category,
            "gender": gender,
            "metrics": list(context.requested_metrics),
            "geography": context.requested_geography,
            "channel": context.requested_channel,
            "query": context.requested_query,
            "period": context.requested_period,
            "population": context.requested_population,
            "unit": context.requested_unit,
            "currency": context.requested_currency,
            "measure_type": context.requested_measure_type,
            "mapping_id": context.requested_mapping_id,
            "mapping_version": context.requested_mapping_version,
            "period_grain": context.requested_period_grain,
            "document_version": context.requested_document_version,
            "inferred_scope": context.inferred_scope or {},
            "explicit_scope": context.explicit_scope or {},
            "intent": context.query_intent,
            "ambiguities": list(context.query_ambiguities),
            "parser_version": context.parser_version,
            "canonical_request": context.canonical_request,
        }

    @staticmethod
    def _validate_upstream_context(context: MarketResearchContext) -> tuple[str, str] | None:
        """Validate only the minimal safe envelope; leave producer payload opaque.

        Producer schema versions are not compared with this agent's local result
        version. The common cross-agent envelope remains unratified.
        """
        if not isinstance(context.planning_request, dict):
            return "UPSTREAM_CONTRACT_INVALID", "planning_request"
        try:
            json.dumps(context.planning_request, allow_nan=False)
        except (TypeError, ValueError):
            return "UPSTREAM_CONTRACT_INVALID", "planning_request"

        request_id = context.request_id
        planning_id = context.planning_request.get("request_id")
        if planning_id is not None:
            if not isinstance(planning_id, str) or not planning_id.strip():
                return "UPSTREAM_REQUEST_ID_INVALID", "planning_request"
            if planning_id != request_id:
                return "UPSTREAM_REQUEST_ID_MISMATCH", "planning_request"

        for name in ("trend_result", "inventory_product_result"):
            payload = getattr(context, name)
            if payload is None:
                continue
            if not isinstance(payload, dict):
                return "UPSTREAM_CONTRACT_INVALID", name
            try:
                json.dumps(payload, allow_nan=False)
            except (TypeError, ValueError):
                return "UPSTREAM_CONTRACT_INVALID", name
            if "result" in payload and not isinstance(payload["result"], dict):
                return "UPSTREAM_CONTRACT_INVALID", name

            nested = payload.get("result") if isinstance(payload.get("result"), dict) else None
            identifiers = [payload[key] for key in ("request_id",) if key in payload]
            if nested is not None and "request_id" in nested:
                identifiers.append(nested["request_id"])
            if not identifiers:
                return "UPSTREAM_REQUEST_ID_INVALID", name
            if any(not isinstance(item, str) or not item.strip() for item in identifiers):
                return "UPSTREAM_REQUEST_ID_INVALID", name
            if any(item != request_id for item in identifiers):
                return "UPSTREAM_REQUEST_ID_MISMATCH", name

            versions = [payload[key] for key in ("schema_version",) if key in payload]
            if nested is not None and "schema_version" in nested:
                versions.append(nested["schema_version"])
            if any(not isinstance(item, str) or not item.strip() for item in versions):
                return "UPSTREAM_CONTRACT_INVALID", name
            metadata = nested if nested is not None else payload
            status = metadata.get("status")
            if not isinstance(status, str) or status not in {item.value for item in ResultStatus}:
                return "UPSTREAM_CONTRACT_INVALID", name
        return None

    @staticmethod
    def _snapshot_upstream_context(context: MarketResearchContext) -> dict:
        """Capture an immutable-before-source-call review snapshot of input JSON."""
        return deepcopy({
            "planning_request": context.planning_request,
            "trend_result": context.trend_result,
            "inventory_product_result": context.inventory_product_result,
        })

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
