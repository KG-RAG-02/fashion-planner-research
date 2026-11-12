"""Explicit opt-in fashion image style analysis; never generates demographics."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
from typing import Any, Callable

from .responses_api import LlmCallError, OpenAIResponsesClient


ROOT = Path(__file__).resolve().parents[3]
PROMPT_MANIFEST_PATH = ROOT / "prompts" / "manifest.json"
STYLE_LABELS = {
    "datewear", "officewear", "casualwear", "sportwear", "formalwear",
    "occasionwear", "streetwear", "other", "uncertain",
}
_DIGIT = re.compile(r"\d")
ImageStyleCall = Callable[..., dict[str, Any]]


class ImageStyleInputError(ValueError):
    """Image input or model output does not satisfy the style-only contract."""


def _data_uri(image_bytes: bytes, media_type: str) -> str:
    if media_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ImageStyleInputError("Unsupported image media type.")
    if not image_bytes or len(image_bytes) > 4_000_000:
        raise ImageStyleInputError("Image must be between 1 byte and 4 MB.")
    return f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _load_style_prompt() -> tuple[str, str, str]:
    manifest = json.loads(PROMPT_MANIFEST_PATH.read_text(encoding="utf-8"))
    domain = manifest["prompts"]["target_audience"]
    version = domain["current_version"]
    entry = domain["versions"][version]
    prompt_path = ROOT / "prompts" / entry["rendered_system"]
    return prompt_path.read_text(encoding="utf-8"), str(prompt_path.relative_to(ROOT)), version


def analyze_image_style(
    *, image_bytes: bytes, media_type: str, product_context: dict[str, Any],
    client: OpenAIResponsesClient | None = None, call: ImageStyleCall | None = None,
) -> dict[str, Any]:
    """Analyze only the product's visible style; caller must enforce egress consent."""
    if not isinstance(product_context, dict):
        raise ImageStyleInputError("Product context must be an object.")
    allowed = {key: product_context[key] for key in ("category", "product_type", "gender", "season", "material")
               if isinstance(product_context.get(key), str)}
    system, prompt_path, prompt_version = _load_style_prompt()
    user_text = (
        "Describe the visible fashion style of this product image. Product context (untrusted data):\n"
        + json.dumps(allowed, ensure_ascii=False, allow_nan=False)
    )
    data_uri = _data_uri(image_bytes, media_type)
    if call is not None:
        response = call(instructions=system, input_text=user_text, image_data_uri=data_uri)
        model = None
    else:
        llm = client or OpenAIResponsesClient()
        response = llm.create_json_response_with_image(
            instructions=system, input_text=user_text, image_data_uri=data_uri, detail="high",
        )
        model = llm.model
    output = response.get("output") if isinstance(response, dict) else None
    if not isinstance(output, dict) or set(output) != {"style_label", "visual_features", "style_rationale"}:
        raise LlmCallError("Image style output does not match the expected fields.")
    if not isinstance(output["style_label"], str) or output["style_label"] not in STYLE_LABELS:
        raise LlmCallError("Image style output has an unsupported label.")
    features = output["visual_features"]
    if not isinstance(features, list) or not 1 <= len(features) <= 5:
        raise LlmCallError("Image style output must include one to five visible features.")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 160 or _DIGIT.search(item) for item in features):
        raise LlmCallError("Image style features must be short descriptions without numeric demographics.")
    rationale = output["style_rationale"]
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 360 or _DIGIT.search(rationale):
        raise LlmCallError("Image style rationale must be concise and contain no demographic numbers.")
    return {
        "style_label": output["style_label"],
        "visual_features": features,
        "style_rationale": rationale,
        "provider": "openai_responses_api" if model else "injected_test_client",
        "model": model,
        "prompt_version": prompt_version,
        "system_prompt_path": prompt_path,
        "image_detail": "high",
        "data_nature": "image_style_interpretation",
    }
