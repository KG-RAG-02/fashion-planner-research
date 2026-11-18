"""Runtime defaults of the research service, overridable through the process environment."""

from __future__ import annotations

import os


# 두 설정은 배포자의 동의가 필요한 동작이라 기본으로 꺼 둔다.
# - H&M Kaggle 자료는 대회 규칙상 비상업 연구에만 쓸 수 있다.
# - 타깃 가설 입력을 외부 LLM 제공자에게 보낸다.
# 켜려면 프로세스 환경변수에 true, 1, yes 중 하나를 넣는다.
DEFAULT_HM_RESEARCH_NONCOMMERCIAL = False
DEFAULT_TARGET_LLM_TRANSFER_AUTHORIZED = False
DEFAULT_USD_KRW_FALLBACK_RATE = "1334.71"
DEFAULT_USD_KRW_FALLBACK_DATE = "2026-11-12"

_TRUE_VALUES = {"1", "true", "yes"}


def _boolean_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip().casefold() in _TRUE_VALUES


def hm_research_noncommercial_enabled() -> bool:
    """Return the code default, with a process-environment override."""
    return _boolean_setting("RESEARCH_HM_NONCOMMERCIAL_RESEARCH", DEFAULT_HM_RESEARCH_NONCOMMERCIAL)


def target_llm_transfer_authorized() -> bool:
    """Return the code default, with a process-environment override."""
    return _boolean_setting(
        "RESEARCH_TARGET_LLM_TRANSFER_AUTHORIZED", DEFAULT_TARGET_LLM_TRANSFER_AUTHORIZED,
    )


def usd_krw_fallback() -> tuple[str, str]:
    """Return the dated FX fallback, allowing explicit process overrides."""
    rate = os.getenv("RESEARCH_USD_KRW_FALLBACK_RATE", "").strip()
    rate_date = os.getenv("RESEARCH_USD_KRW_FALLBACK_DATE", "").strip()
    return (rate or DEFAULT_USD_KRW_FALLBACK_RATE, rate_date or DEFAULT_USD_KRW_FALLBACK_DATE)
