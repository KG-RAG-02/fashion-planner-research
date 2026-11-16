"""Bounded HTTPFS settings shared by H&M read-only research queries."""

from __future__ import annotations

import os
from typing import Any


def configure_httpfs(connection: Any) -> None:
    """Use bounded per-request timeouts and retries for proxy/R2 reliability."""
    try:
        timeout = int(os.getenv("VOGUE_HM_HTTP_TIMEOUT_SECONDS", "20"))
        retries = int(os.getenv("VOGUE_HM_HTTP_RETRIES", "1"))
    except ValueError:
        raise ValueError("VOGUE_HM_HTTP_TIMEOUT_SECONDS and VOGUE_HM_HTTP_RETRIES must be integers.") from None
    if not 5 <= timeout <= 120 or not 0 <= retries <= 5:
        raise ValueError("H&M HTTP timeout or retry count is outside its supported bounds.")
    connection.execute(f"SET http_timeout = {timeout}")
    connection.execute(f"SET http_retries = {retries}")
