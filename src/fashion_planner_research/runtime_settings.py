"""Runtime defaults of the research service, overridable through the process environment."""

from __future__ import annotations

import os


DEFAULT_HM_RESEARCH_NONCOMMERCIAL = True
DEFAULT_TARGET_LLM_TRANSFER_AUTHORIZED = True
DEFAULT_USD_KRW_FALLBACK_RATE = "1334.71"
DEFAULT_USD_KRW_FALLBACK_DATE = "2026-11-12"

_TRUE_VALUES = {"1", "true", "yes"}


def _boolean_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip().casefold() in _TRUE_VALUES


def hm_research_noncommercial_enabled() -> bool:
    """Return the code default, with a process-environment override."""
    return _boolean_setting("VOGUE_HM_RESEARCH_NONCOMMERCIAL", DEFAULT_HM_RESEARCH_NONCOMMERCIAL)


def target_llm_transfer_authorized() -> bool:
    """Return the code default, with a process-environment override."""
    return _boolean_setting(
        "VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED", DEFAULT_TARGET_LLM_TRANSFER_AUTHORIZED,
    )


def usd_krw_fallback() -> tuple[str, str]:
    """Return the dated FX fallback, allowing explicit process overrides."""
    rate = os.getenv("VOGUE_USD_KRW_FALLBACK_RATE", "").strip()
    rate_date = os.getenv("VOGUE_USD_KRW_FALLBACK_DATE", "").strip()
    return (rate or DEFAULT_USD_KRW_FALLBACK_RATE, rate_date or DEFAULT_USD_KRW_FALLBACK_DATE)
