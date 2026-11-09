"""Test-only adapter for exercising the MarketEvidenceRetriever query port.

Synthetic fixture hits deliberately remain dictionaries with a distinct
test-only permission label. ``retrieve`` never promotes them into production
``EvidenceChunk``/``RetrievedEvidence`` records, so the injected workflow
fails closed while query forwarding is exercised end to end.
"""

from __future__ import annotations

from typing import Any, Mapping

from market_research_agent.sources.market.evidence_retrieval import (
    MarketEvidenceRetriever,
    RetrievalQuery,
    RetrievedEvidence,
)

from .evaluate import lexical_retrieve
from .generate import TEST_PERMISSION


class SyntheticMarketEvidenceRetriever(MarketEvidenceRetriever):
    """Synthetic search probe; never returns production-authorized evidence."""

    def __init__(self, bundle: Mapping[str, Any]) -> None:
        self._bundle = bundle
        self.last_query: RetrievalQuery | None = None
        self.last_test_result: dict[str, Any] | None = None

    @staticmethod
    def _fixture_query(query: RetrievalQuery) -> dict[str, Any]:
        scope = {
            "category": query.category,
            "geography": query.geography,
            "channel": query.channel,
            "metrics": list(query.metrics),
            "as_of_or_period": query.as_of_or_period,
            "population": query.population,
            "unit": query.unit,
            "currency": query.currency,
            "measure_type": query.measure_type,
            "mapping_id": query.mapping_id,
            "mapping_version": query.mapping_version,
            "period_grain": query.period_grain,
            "document_version": query.document_version,
        }
        scope = {key: value for key, value in scope.items() if value not in (None, (), [])}
        return {"raw_query": query.raw_query, "scope": scope}

    def search_synthetic(
        self, query: RetrievalQuery, *, test_permission_scope: str | None = None,
    ) -> dict[str, Any]:
        """Return explicitly test-only matches for the fixture evaluator."""
        self.last_query = query
        fixture = self._fixture_query(query)
        if test_permission_scope is not None:
            fixture["scope"]["simulated_source_permission_status"] = test_permission_scope
        return lexical_retrieve(self._bundle, fixture, limit=query.limit)

    def retrieve(self, query: RetrievalQuery) -> tuple[RetrievedEvidence, ...]:
        """Conform to the production port but fail closed at its trust boundary.

        The synthetic result remains available in ``last_test_result`` for test
        assertions only. No fixture hit is converted to production Evidence.
        """
        self.last_query = query
        self.last_test_result = self.search_synthetic(query)
        if self.last_test_result.get("permission_status") != TEST_PERMISSION:
            raise ValueError("Synthetic retrieval result lost its test-only permission marker.")
        return ()


def retrieval_query_from_fixture(fixture: Mapping[str, Any], *, limit: int = 8) -> RetrievalQuery:
    scope = fixture.get("scope", {})
    return RetrievalQuery(
        raw_query=str(fixture.get("raw_query", "")),
        category=scope.get("category"), geography=scope.get("geography"),
        channel=scope.get("channel"), metrics=tuple(scope.get("metrics", ())),
        as_of_or_period=scope.get("as_of_or_period"), limit=limit,
        population=scope.get("population"), unit=scope.get("unit"),
        currency=scope.get("currency"), measure_type=scope.get("measure_type"),
        mapping_id=scope.get("mapping_id"), mapping_version=scope.get("mapping_version"),
        period_grain=scope.get("period_grain"), document_version=scope.get("document_version"),
    )
