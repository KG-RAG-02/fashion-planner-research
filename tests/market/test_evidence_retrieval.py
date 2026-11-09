from __future__ import annotations

import unittest

from market_research_agent.domain.models import PermissionStatus
from market_research_agent.sources.market.evidence_retrieval import (
    EvidenceChunk,
    RetrievalQuery,
    RetrievedEvidence,
    authorized_chunks,
)


class EvidenceRetrievalContractTests(unittest.TestCase):
    def _chunk(self, permission=PermissionStatus.AUTHORIZED):
        return EvidenceChunk(
            evidence_id="synthetic-E1", source_id="synthetic-source",
            source_name="Synthetic approved methodology fixture", text="Synthetic text only",
            locator="synthetic://page/1", permission_status=permission,
            geography="KR", channel="online", category="Outerwear",
            document_version="fixture-v1",
        )

    def test_only_authorized_documents_can_enter_retrieval_corpus(self):
        authorized = self._chunk()
        unknown = self._chunk(PermissionStatus.UNKNOWN)
        denied = self._chunk(PermissionStatus.DENIED)
        self.assertEqual(authorized_chunks((authorized, unknown, denied)), (authorized,))

    def test_retrieval_requires_original_user_query(self):
        with self.assertRaises(ValueError):
            RetrievalQuery(raw_query="  ")
        query = RetrievalQuery("한국 온라인 시장 규모", category="Outerwear", geography="KR")
        self.assertEqual(query.raw_query, "한국 온라인 시장 규모")

    def test_returned_evidence_must_be_authorized(self):
        with self.assertRaises(ValueError):
            RetrievedEvidence(self._chunk(PermissionStatus.UNKNOWN), 0.5)


if __name__ == "__main__":
    unittest.main()
