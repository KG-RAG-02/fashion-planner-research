"""Permission-gated retrieval port for explanatory market evidence.

This module intentionally provides an interface and validated records only.
It does not select an embedding model, vector store, external provider, or
perform indexing. Those choices require source-owner and operator approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...domain.models import PermissionStatus


@dataclass(frozen=True, slots=True)
class EvidenceChunk:
    evidence_id: str
    source_id: str
    source_name: str
    text: str
    locator: str
    permission_status: PermissionStatus
    published_at: str | None = None
    observed_at: str | None = None
    fetched_at: str | None = None
    geography: str | None = None
    channel: str | None = None
    category: str | None = None
    document_version: str | None = None

    def __post_init__(self) -> None:
        required = (self.evidence_id, self.source_id, self.source_name, self.text, self.locator)
        if any(not value.strip() for value in required):
            raise ValueError("Evidence chunks require source identity, text, and a locator.")


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    raw_query: str
    category: str | None = None
    geography: str | None = None
    channel: str | None = None
    metrics: tuple[str, ...] = ()
    as_of_or_period: str | None = None
    limit: int = 8

    def __post_init__(self) -> None:
        if not self.raw_query.strip():
            raise ValueError("raw_query is required for evidence retrieval.")
        if self.limit < 1:
            raise ValueError("limit must be at least one.")


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    chunk: EvidenceChunk
    score: float

    def __post_init__(self) -> None:
        if self.chunk.permission_status != PermissionStatus.AUTHORIZED:
            raise ValueError("Retrieved evidence must have confirmed authorized permission.")


class MarketEvidenceRetriever(Protocol):
    """Search authorized explanatory documents; never compute business metrics."""

    def retrieve(self, query: RetrievalQuery) -> tuple[RetrievedEvidence, ...]: ...


def authorized_chunks(chunks: tuple[EvidenceChunk, ...]) -> tuple[EvidenceChunk, ...]:
    """Fail closed at ingestion: unknown or denied material is excluded."""
    return tuple(chunk for chunk in chunks if chunk.permission_status == PermissionStatus.AUTHORIZED)
