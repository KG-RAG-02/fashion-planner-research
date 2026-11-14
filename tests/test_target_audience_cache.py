from __future__ import annotations

import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch

from market_research_agent.llm.responses_api import OpenAIResponsesClient
from market_research_agent.llm import target_audience


VALID_OUTPUT = {
    "proposal": "A research hypothesis.",
    "primary_segment": "Customers seeking practical outerwear.",
    "secondary_segments": ["Layering-focused customers"],
    "age_evidence": "Synthetic aggregate only.",
    "product_fit": {
        "style_direction": "Practical layers",
        "wearing_occasions": ["Commute", "Weekend"],
        "needs_hypotheses": ["Warmth", "Easy care"],
        "styling_cues": ["Layered", "Neutral"],
    },
    "price_positioning": {"position": "mid_market", "rationale": "Synthetic reference.", "confidence": "low"},
    "merchandising_actions": ["Interview buyers", "Review fit"],
    "channel_content_hypotheses": ["Show layering", "Explain materials"],
    "validation_plan": ["Interview", "Test samples", "Review response"],
    "evidence_basis": ["Historical aggregate", "Synthetic fixture"],
    "limitations": ["Historical only", "Not demand evidence"],
}


class TargetAudienceCacheTests(unittest.TestCase):
    def setUp(self):
        target_audience._LLM_CACHE.clear()
        target_audience._LLM_CACHE_INFLIGHT.clear()

    def tearDown(self):
        target_audience._LLM_CACHE.clear()
        target_audience._LLM_CACHE_INFLIGHT.clear()

    def test_client_reads_llm_api_key_environment_variable(self):
        with patch.dict("os.environ", {"LLM_API_KEY": "synthetic-env-key", "LLM_MODEL": "synthetic-model"}, clear=True):
            client = OpenAIResponsesClient()
        self.assertEqual(client.api_key, "synthetic-env-key")
        self.assertEqual(client.model, "synthetic-model")

    def test_client_reports_missing_llm_api_key_without_sending_request(self):
        from market_research_agent.llm.responses_api import LlmCallError

        with patch.dict("os.environ", {"LLM_MODEL": "synthetic-model"}, clear=True):
            with self.assertRaisesRegex(LlmCallError, "LLM_API_KEY is required"):
                OpenAIResponsesClient()

    def test_client_reports_missing_llm_model(self):
        from market_research_agent.llm.responses_api import LlmCallError

        with patch.dict("os.environ", {"LLM_API_KEY": "synthetic-env-key"}, clear=True):
            with self.assertRaisesRegex(LlmCallError, "LLM_MODEL is required"):
                OpenAIResponsesClient()

    def test_exact_prompt_and_model_reuses_validated_output(self):
        client = OpenAIResponsesClient(api_key="synthetic-key", model="synthetic-model")
        with patch.object(client, "create_json_response", side_effect=lambda **_kwargs: {"output": deepcopy(VALID_OUTPUT)}) as call:
            first = target_audience.propose_target_audience(
                product_context={"category": "Outerwear", "material": "wool"},
                customer_profile=None,
                client=client,
            )
            second = target_audience.propose_target_audience(
                product_context={"category": "Outerwear", "material": "wool"},
                customer_profile=None,
                client=client,
            )
        self.assertEqual(first, second)
        self.assertEqual(first["type"], "hypothesis")
        self.assertEqual(call.call_count, 1)

    def test_changed_source_context_or_credential_does_not_reuse_result(self):
        first_client = OpenAIResponsesClient(api_key="synthetic-key-a", model="synthetic-model")
        second_client = OpenAIResponsesClient(api_key="synthetic-key-b", model="synthetic-model")
        with patch.object(first_client, "create_json_response", side_effect=lambda **_kwargs: {"output": deepcopy(VALID_OUTPUT)}) as first_call, \
             patch.object(second_client, "create_json_response", side_effect=lambda **_kwargs: {"output": deepcopy(VALID_OUTPUT)}) as second_call:
            target_audience.propose_target_audience(
                product_context={"category": "Outerwear", "material": "wool"}, client=first_client,
                customer_profile=None,
            )
            target_audience.propose_target_audience(
                product_context={"category": "Outerwear", "material": "cotton"}, client=first_client,
                customer_profile=None,
            )
            target_audience.propose_target_audience(
                product_context={"category": "Outerwear", "material": "wool"}, client=second_client,
                customer_profile=None,
            )
        self.assertEqual(first_call.call_count, 2)
        self.assertEqual(second_call.call_count, 1)

    def test_concurrent_duplicate_prompt_shares_one_provider_call(self):
        client = OpenAIResponsesClient(api_key="synthetic-key", model="synthetic-model")

        def fake_call(**_kwargs):
            time.sleep(0.03)
            return {"output": deepcopy(VALID_OUTPUT)}

        def invoke():
            return target_audience.propose_target_audience(
                product_context={"category": "Outerwear", "material": "wool"},
                customer_profile=None, client=client,
            )

        with patch.object(client, "create_json_response", side_effect=fake_call) as provider_call:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first, second = tuple(executor.map(lambda _index: invoke(), range(2)))
        self.assertEqual(first, second)
        self.assertEqual(provider_call.call_count, 1)

    def test_llm_payload_excludes_r2_object_paths_and_non_contract_fields(self):
        captured = {}

        def fake_call(**kwargs):
            captured.update(kwargs)
            return {"output": deepcopy(VALID_OUTPUT)}

        target_audience.propose_target_audience(
            product_context={"category": "Outerwear", "material": "wool", "proposed_price": 42_000},
            customer_profile={
                "category": "Outerwear", "unique_customer_denominator": 123,
                "sources": [{"object_key": "private/customer-source.parquet"}],
            },
            market_price_reference={
                "sample_count": 456,
                "sources": [{"object_key": "private/transaction-source.parquet"}],
            },
            client=OpenAIResponsesClient(api_key="synthetic-key", model="synthetic-model"),
            call=fake_call,
        )
        sent = captured["input_text"]
        self.assertIn('"unique_customer_denominator": 123', sent)
        self.assertIn('"sample_count": 456', sent)
        self.assertNotIn("private/customer-source.parquet", sent)
        self.assertNotIn("private/transaction-source.parquet", sent)
        self.assertNotIn("proposed_price", sent)


if __name__ == "__main__":
    unittest.main()
