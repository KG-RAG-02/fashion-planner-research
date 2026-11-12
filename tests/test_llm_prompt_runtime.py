from __future__ import annotations

import io
import json

from market_research_agent.llm import analysis
from market_research_agent.llm import responses_api
from market_research_agent.llm.responses_api import LlmCallError, OpenAIResponsesClient


def test_model_is_required_from_env_when_no_explicit_override(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    try:
        OpenAIResponsesClient(api_key="test-key")
    except LlmCallError as exc:
        assert "OPENAI_MODEL is required" in str(exc)
    else:
        raise AssertionError("missing OPENAI_MODEL should be rejected")


def test_model_environment_override(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "approved-model-override")
    client = OpenAIResponsesClient(api_key="test-key")
    assert client.model == "approved-model-override"


def test_domain_calls_use_rendered_system_prompt_and_report_model(monkeypatch):
    captured = []

    def fake_call(*, instructions, input_text):
        captured.append((instructions, input_text))
        return {
            "output": {
                "status": "no_results",
                "summary": "No eligible evidence.",
                "findings": [],
                "limitations": [],
                "missing_data": [],
            }
        }

    for domain in ("sales_analysis", "market_analysis"):
        expected = (analysis.PROMPT_ROOT / domain / "v1/rendered_system.md").read_text(encoding="utf-8")
        monkeypatch.setattr(
            analysis,
            "_load_prompt",
            lambda requested_domain, expected=expected: (
                expected, "Input: {{ANALYSIS_INPUT_JSON}}", f"{requested_domain}/v1/rendered_system.md"
            ),
        )
        result = analysis._call_domain(
            domain=domain,
            payload={"request_id": f"test-{domain}"},
            allowed_evidence_ids=set(),
            call=fake_call,
        )
        assert captured[-1][0].startswith(expected)
        assert result["llm"]["prompt_version"] == "v1"


def test_responses_request_sends_model_and_system_instructions(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "id": "resp-test",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
            }).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(responses_api, "urlopen", fake_urlopen)
    client = OpenAIResponsesClient(api_key="test-key")
    client.create_json_response(instructions="trusted system prompt", input_text="untrusted input")
    assert captured["body"]["model"] == "gpt-6-luna"
    assert captured["body"]["instructions"] == "trusted system prompt"
    assert captured["body"]["input"] == "untrusted input"
    assert captured["body"]["store"] is False


def test_explicit_client_model_is_preserved_over_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    client = OpenAIResponsesClient(api_key="test-key", model="explicit-model")
    assert client.model == "explicit-model"
