"""Small, dependency-free OpenAI Responses API client for explicit local runs.

This client has no tools, conversation state, retry loop, or request logging.
It sends only the caller-provided prompt and data to the fixed OpenAI API host.
"""

from __future__ import annotations

import json
import os
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RESPONSES_URL = "https://api.openai.com/v1/responses"


class LlmCallError(RuntimeError):
    """Safe-to-display LLM configuration or provider error."""


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 60,
        max_output_tokens: int = 1800,
    ) -> None:
        self.api_key = api_key if api_key is not None else (
            os.environ.get("OPENAI_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
        )
        configured_model = os.environ.get("OPENAI_MODEL", "").strip()
        self.model = model if model is not None else configured_model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        if not self.api_key:
            raise LlmCallError("OPENAI_API_KEY is required; no request was sent.")
        if not self.model:
            raise LlmCallError("OPENAI_MODEL is required; no request was sent.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")

    def create_json_response(self, *, instructions: str, input_text: str) -> dict[str, Any]:
        return self._create_json_response(instructions=instructions, input_payload=input_text)

    def create_json_response_with_image(
        self, *, instructions: str, input_text: str, image_data_uri: str, detail: str = "high",
    ) -> dict[str, Any]:
        """Send one caller-provided image using the Responses API vision input format."""
        if not image_data_uri.startswith(("data:image/jpeg;base64,", "data:image/png;base64,",
                                          "data:image/webp;base64,")):
            raise LlmCallError("Image input must be a supported base64 data URL.")
        if detail not in {"low", "high", "auto"}:
            raise LlmCallError("Image detail setting is unsupported.")
        return self._create_json_response(
            instructions=instructions,
            input_payload=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": input_text},
                    {"type": "input_image", "image_url": image_data_uri, "detail": detail},
                ],
            }],
        )

    def _create_json_response(self, *, instructions: str, input_payload: Any) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.model,
                "instructions": instructions,
                "input": input_payload,
                "store": False,
                "max_output_tokens": self.max_output_tokens,
                "text": {"format": {"type": "json_object"}},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            RESPONSES_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            try:
                import certifi
                tls_context = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                tls_context = ssl.create_default_context()
            with urlopen(request, timeout=self.timeout_seconds, context=tls_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Provider response bodies may echo request details. Never surface them.
            raise LlmCallError(f"LLM provider returned HTTP {exc.code}.") from None
        except (URLError, TimeoutError, OSError):
            raise LlmCallError("LLM provider could not be reached or timed out.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LlmCallError("LLM provider returned an invalid response.") from None

        text = _extract_output_text(payload)
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            raise LlmCallError("LLM response was not valid JSON.") from None
        if not isinstance(result, dict):
            raise LlmCallError("LLM response JSON must be an object.")
        return {"response_id": payload.get("id"), "output": result}


def _extract_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise LlmCallError("LLM provider returned an invalid response.")
    if payload.get("status") in {"failed", "cancelled", "incomplete"}:
        raise LlmCallError("LLM response did not complete successfully.")
    pieces: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                value = content.get("text")
                if isinstance(value, str):
                    pieces.append(value)
    if not pieces:
        raise LlmCallError("LLM response contained no output text.")
    return "\n".join(pieces)

