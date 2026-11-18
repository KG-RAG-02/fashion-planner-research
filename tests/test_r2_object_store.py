from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from fashion_planner_research.sources.shared.r2_object_store import (
    R2ConfigurationError,
    R2ObjectError,
    R2ObjectStore,
    R2Settings,
)


class FakeR2Client:
    def __init__(self, body: bytes | Exception):
        self.body = body
        self.calls = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.body, Exception):
            raise self.body
        return {"ContentLength": len(self.body), "Body": io.BytesIO(self.body)}


class ReadFailureBody:
    def __init__(self):
        self.closed = False

    def read(self, _size):
        raise OSError("simulated stream failure")

    def close(self):
        self.closed = True


class ReadFailureClient:
    def __init__(self, body):
        self.body = body

    def get_object(self, **_kwargs):
        return {"ContentLength": 1, "Body": self.body}


class R2ObjectStoreTests(unittest.TestCase):
    def settings(self, endpoint=""):
        return R2Settings("account", "bucket", "access-secret-id", "never-log-this-secret", endpoint)

    def test_reads_bounded_objects_and_parses_json(self):
        client = FakeR2Client(b'{"ok":true}')
        store = R2ObjectStore(self.settings(), client)
        self.assertEqual(store.get_json("prefix/manifest.json"), {"ok": True})
        self.assertEqual(len(client.calls), 1)

    def test_does_not_expose_credentials_or_sdk_error_body(self):
        secret = "never-log-this-secret"
        store = R2ObjectStore(self.settings(), FakeR2Client(RuntimeError(secret)))
        with self.assertRaises(R2ObjectError) as raised:
            store.get_bytes("safe/key")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, repr(self.settings()))

    def test_rejects_unsafe_key_endpoint_and_oversize_object(self):
        with self.assertRaises(R2ObjectError):
            R2ObjectStore(self.settings(), FakeR2Client(b"x")).get_bytes("../secret")
        with self.assertRaises(R2ConfigurationError):
            R2Settings("account", "bucket", "a", "s", "http://r2.example")
        with self.assertRaises(R2ObjectError):
            R2ObjectStore(self.settings(), FakeR2Client(b"1234")).get_bytes("safe/key", max_bytes=3)

    def test_reads_only_expected_environment_names(self):
        with patch.dict("os.environ", {
            "R2_ACCOUNT_ID": "account", "R2_BUCKET": "bucket",
            "R2_ACCESS_KEY_ID": "key", "R2_SECRET_ACCESS_KEY": "secret",
            "R2_ENDPOINT": "https://r2.example/",
        }, clear=True):
            settings = R2Settings.from_env()
        self.assertEqual(settings.endpoint_url, "https://r2.example")

    def test_stream_is_closed_when_body_read_fails(self):
        body = ReadFailureBody()
        store = R2ObjectStore(self.settings(), ReadFailureClient(body))
        with self.assertRaises(R2ObjectError):
            store.get_bytes("prefix/object")
        self.assertTrue(body.closed)

    def test_botocore_connection_failures_are_classified_without_details(self):
        failure_type = type("EndpointConnectionError", (Exception,), {})
        store = R2ObjectStore(self.settings(), FakeR2Client(failure_type("secret endpoint text")))
        with self.assertRaises(R2ObjectError) as raised:
            store.get_bytes("prefix/object")
        self.assertEqual(raised.exception.failure_code, "NetworkConnectionError")
        self.assertNotIn("secret endpoint text", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
