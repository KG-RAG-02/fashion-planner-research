"""Reference FX lookup and deterministic USD-to-KRW conversion."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
import json
import math
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..runtime_settings import usd_krw_fallback


FRANKFURTER_RATE_URL = "https://api.frankfurter.dev/v2/rate/usd/krw"


class CurrencyConversionError(ValueError):
    """A current reference rate could not be retrieved or validated."""


def convert_usd_to_krw(
    amount_usd: int | float | Decimal,
    rate: int | float | Decimal,
    *,
    round_to_nearest_krw: int = 1000,
) -> dict[str, Any]:
    """Convert USD using a supplied KRW-per-USD rate and round half up."""
    try:
        amount = Decimal(str(amount_usd))
        fx = Decimal(str(rate))
        rounding_unit = Decimal(round_to_nearest_krw)
    except (InvalidOperation, ValueError):
        raise CurrencyConversionError("Amount, rate, or rounding unit is invalid.") from None
    if (not amount.is_finite() or amount < 0 or not fx.is_finite() or fx <= 0
            or not rounding_unit.is_finite() or rounding_unit <= 0):
        raise CurrencyConversionError("Amount, rate, and rounding unit must be finite and valid.")
    exact = amount * fx
    rounded = (exact / rounding_unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * rounding_unit
    return {
        "amount_usd": float(amount),
        "rate_krw_per_usd": float(fx),
        "converted_amount_krw": round(float(exact), 2),
        "suggested_price_krw": int(rounded),
        "rounding": f"nearest {round_to_nearest_krw} KRW; half up",
    }


def fetch_usd_krw_reference_rate(*, timeout_seconds: float = 8) -> dict[str, Any]:
    """Fetch the latest published USD/KRW reference rate from Frankfurter."""
    request = Request(FRANKFURTER_RATE_URL, headers={"Accept": "application/json"})
    try:
        try:
            import certifi
            tls_context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            tls_context = ssl.create_default_context()
        with urlopen(request, timeout=timeout_seconds, context=tls_context) as response:
            payload = json.loads(response.read(64_000).decode("utf-8"))
    except HTTPError as exc:
        if exc.code < 500 and exc.code != 429:
            raise CurrencyConversionError(f"FX provider returned HTTP {exc.code}.") from None
        return _configured_fallback_rate()
    except (URLError, TimeoutError, OSError):
        return _configured_fallback_rate()
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CurrencyConversionError("FX provider returned invalid JSON.") from None
    rate = payload.get("rate") if isinstance(payload, dict) else None
    rate_date = payload.get("date") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or payload.get("base") != "USD" or payload.get("quote") != "KRW"
            or isinstance(rate, bool) or not isinstance(rate, (int, float))
            or not math.isfinite(rate) or rate <= 0
            or not isinstance(rate_date, str) or len(rate_date) != 10):
        raise CurrencyConversionError("FX provider response did not contain a valid USD/KRW rate.")
    return {
        "base_currency": "USD",
        "quote_currency": "KRW",
        "rate": float(rate),
        "rate_date": rate_date,
        "provider": "Frankfurter reference rates",
        "source_url": FRANKFURTER_RATE_URL,
        "rate_type": "reference_rate_not_live_quote",
        "fallback_used": False,
    }


def _configured_fallback_rate() -> dict[str, Any]:
    """Use the service's dated reference fallback when the provider is unavailable."""
    raw_rate, rate_date = usd_krw_fallback()
    try:
        rate = float(raw_rate)
        observed = date.fromisoformat(rate_date)
    except (TypeError, ValueError):
        raise CurrencyConversionError("FX provider could not be reached and the dated fallback rate is invalid.") from None
    if not math.isfinite(rate) or rate <= 0 or observed > date.today():
        raise CurrencyConversionError("The dated USD/KRW fallback rate is invalid.")
    age_days = (date.today() - observed).days
    return {
        "base_currency": "USD",
        "quote_currency": "KRW",
        "rate": rate,
        "rate_date": observed.isoformat(),
        "provider": "dated fallback; Frankfurter reference observation",
        "source_url": FRANKFURTER_RATE_URL,
        "rate_type": "reference_rate_not_live_quote",
        "fallback_used": True,
        "fallback_age_days": age_days,
        "stale": age_days > 7,
    }
