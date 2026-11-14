"""Read-only Cloudflare R2 access through its S3-compatible API.

This module never loads dotenv files and never writes or deletes objects.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from typing import Any


class R2ConfigurationError(ValueError):
    """R2 settings are missing or unsafe."""


class R2ObjectError(ValueError):
    """An R2 object could not be safely read."""

    def __init__(self, message: str, *, failure_code: str = "R2ReadFailed") -> None:
        super().__init__(message)
        self.failure_code = failure_code


def _safe_failure_code(exc: Exception) -> str:
    """Return an allowlisted storage failure category without SDK details."""
    exception_name = type(exc).__name__
    if exception_name in {"EndpointConnectionError", "ConnectionClosedError", "ProxyConnectionError", "SSLError"}:
        return "NetworkConnectionError"
    if exception_name in {"ConnectTimeoutError", "ReadTimeoutError"}:
        return "NetworkTimeout"
    if exception_name in {"NoCredentialsError", "PartialCredentialsError"}:
        return "MissingCredentials"
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error", {})
        code = str(error.get("Code", "")) if isinstance(error, dict) else ""
        metadata = response.get("ResponseMetadata", {})
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        if code in {"AccessDenied", "Forbidden", "InvalidAccessKeyId", "SignatureDoesNotMatch"} or status == 403:
            return "AccessDenied"
        if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
            return "ObjectNotFound"
        if code in {"RequestTimeout", "RequestTimeoutException", "SlowDown"} or status == 429:
            return "NetworkTimeoutOrThrottled"
        if isinstance(status, int) and status >= 500:
            return "R2ServiceUnavailable"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "NetworkTimeout"
    if isinstance(exc, PermissionError):
        return "AccessDenied"
    return "R2ReadFailed"


@dataclass(frozen=True)
class R2Settings:
    account_id: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    endpoint: str = ""

    def __post_init__(self) -> None:
        if not all((self.account_id, self.bucket, self.access_key_id, self.secret_access_key)):
            raise R2ConfigurationError("R2 read configuration is missing required settings.")
        if any(character in self.account_id for character in "/:@?#"):
            raise R2ConfigurationError("R2 account identifier is invalid.")
        if self.endpoint:
            parsed = urlsplit(self.endpoint.rstrip("/"))
            if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or parsed.path):
                raise R2ConfigurationError("R2 endpoint must be an HTTPS origin without embedded credentials or path.")

    @property
    def endpoint_url(self) -> str:
        return self.endpoint or f"https://{self.account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_env(cls) -> "R2Settings":
        values = {
            "account_id": os.getenv("R2_ACCOUNT_ID", "").strip(),
            "bucket": os.getenv("R2_BUCKET", "").strip(),
            "access_key_id": os.getenv("R2_ACCESS_KEY_ID", "").strip(),
            "secret_access_key": os.getenv("R2_SECRET_ACCESS_KEY", "").strip(),
            "endpoint": os.getenv("R2_ENDPOINT", "").strip().rstrip("/"),
        }
        missing = [name for name, value in values.items() if name != "endpoint" and not value]
        if missing:
            raise R2ConfigurationError("R2 read configuration is missing required settings.")
        return cls(**values)


class R2ObjectStore:
    """Small read-only S3 client with bounded object reads and safe errors."""

    def __init__(self, settings: R2Settings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client

    @classmethod
    def from_env(cls, *, client: Any | None = None) -> "R2ObjectStore":
        return cls(R2Settings.from_env(), client=client)

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on optional installation
            raise R2ConfigurationError("R2 reads require the optional boto3 dependency.") from exc
        self._client = boto3.client(
            "s3", endpoint_url=self.settings.endpoint_url, region_name="auto",
            aws_access_key_id=self.settings.access_key_id,
            aws_secret_access_key=self.settings.secret_access_key,
            config=Config(connect_timeout=5, read_timeout=30,
                          retries={"max_attempts": 2, "mode": "standard"}),
        )
        return self._client

    def get_bytes(self, key: str, *, max_bytes: int = 64_000_000) -> bytes:
        content, _metadata = self.get_bytes_and_metadata(key, max_bytes=max_bytes)
        return content

    def get_bytes_and_metadata(self, key: str, *, max_bytes: int = 64_000_000) -> tuple[bytes, dict[str, str]]:
        if not isinstance(key, str) or not key or key.startswith("/") or ".." in key.split("/"):
            raise R2ObjectError("R2 object key is invalid.")
        try:
            response = self._get_client().get_object(Bucket=self.settings.bucket, Key=key)
            expected = response.get("ContentLength")
            if expected is not None and (expected < 0 or expected > max_bytes):
                raise R2ObjectError("R2 object exceeds the configured read limit.", failure_code="ObjectSizeLimitExceeded")
            body = response["Body"]
            try:
                content = body.read(max_bytes + 1)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            if len(content) > max_bytes or (expected is not None and len(content) != expected):
                raise R2ObjectError("R2 object size validation failed.", failure_code="ObjectSizeMismatch")
            metadata = response.get("Metadata", {})
            if not isinstance(metadata, dict):
                raise R2ObjectError("R2 object metadata is invalid.")
            return content, {str(name).casefold(): str(value) for name, value in metadata.items()}
        except R2ObjectError:
            raise
        except Exception as exc:
            # Do not include SDK exception text: it can contain internal endpoint or request data.
            raise R2ObjectError(
                f"R2 object read failed ({_safe_failure_code(exc)}).",
                failure_code=_safe_failure_code(exc),
            ) from None

    def get_json(self, key: str, *, max_bytes: int = 8_000_000) -> dict[str, Any]:
        try:
            value = json.loads(self.get_bytes(key, max_bytes=max_bytes))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise R2ObjectError("R2 JSON object is invalid.") from None
        if not isinstance(value, dict):
            raise R2ObjectError("R2 JSON object must contain an object value.")
        return value
