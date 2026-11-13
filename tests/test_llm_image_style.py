from __future__ import annotations

import json

from market_research_agent.llm import responses_api
from market_research_agent.llm.image_style import analyze_image_style
from market_research_agent.llm.responses_api import LlmCallError, OpenAIResponsesClient


def test_image_style_uses_versioned_system_prompt_and_rejects_demographic_claims():
    captured = {}

    def fake_call(*, instructions, input_text, image_data_uri):
        captured.update(instructions=instructions, input_text=input_text, image=image_data_uri)
        return {"output": {
            "style_label": "datewear",
            "visual_features": ["부드러운 색감", "허리선이 강조된 실루엣"],
            "style_rationale": "부드러운 색과 허리선 중심의 실루엣이 데이트 스타일에 어울립니다.",
        }}

    result = analyze_image_style(
        image_bytes=b"fake-image", media_type="image/jpeg",
        product_context={"category": "Outerwear", "gender": "women", "sensitive": "must not pass"},
        call=fake_call,
    )
    assert result["style_label"] == "datewear"
    assert "sensitive" not in captured["input_text"]
    assert captured["instructions"].startswith("You are the visual-style analyst")
    assert captured["image"].startswith("data:image/jpeg;base64,")

    def bad_call(**_kwargs):
        return {"output": {
            "style_label": "datewear", "visual_features": ["20대 선호"],
            "style_rationale": "20대가 좋아할 룩입니다.",
        }}

    try:
        analyze_image_style(image_bytes=b"image", media_type="image/jpeg", product_context={}, call=bad_call)
    except LlmCallError:
        pass
    else:
        raise AssertionError("numeric demographic claims from the model must be rejected")


def test_responses_client_sends_image_as_input_image(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    captured = {}

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return json.dumps({"id": "resp-image", "status": "completed",
                               "output": [{"type": "message", "content": [
                                   {"type": "output_text", "text": "{}"}]}]}).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr(responses_api, "urlopen", fake_urlopen)
    client = OpenAIResponsesClient(api_key="test-key")
    client.create_json_response_with_image(
        instructions="system", input_text="analyze", image_data_uri="data:image/png;base64,aGk=",
    )
    image_input = captured["body"]["input"][0]["content"][1]
    assert captured["body"]["model"] == "gpt-6-luna"
    assert image_input["type"] == "input_image"
    assert image_input["detail"] == "high"
