"""H&M processed-object reader for the owner-enabled R2 data bundle."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from ....sources.shared.r2_object_store import R2ObjectError, R2ObjectStore
from .hm_sales_adapter import HmHistoricalSalesSource, HmSourceReadError

_REQUIRED_FILES = {"manifest.json", "article_catalog.csv", "weekly_sales_by_article_channel.csv"}
_MAX_HM_PROCESSED_OBJECT_BYTES = 256_000_000


class HmR2BundleError(HmSourceReadError):
    """H&M R2 bundle is unavailable or failed integrity checks."""


class R2HmHistoricalSalesSource(HmHistoricalSalesSource):
    """Read the existing H&M processed schema from explicitly configured R2 objects."""

    def __init__(self, category_map, mapping_version: str, *, usage_authorized: bool,
                 store: R2ObjectStore | None = None, manifest_key: str | None = None,
                 allowed_prefix: str | None = None) -> None:
        prefix = (allowed_prefix or os.getenv("PROJECT07_HM_R2_PREFIX", "")).strip().strip("/")
        key = (manifest_key or os.getenv("PROJECT07_HM_R2_MANIFEST_KEY", "")).strip()
        if not prefix or not key:
            raise HmR2BundleError("Explicit H&M R2 prefix and bundle manifest key are required.")
        manifest_path = PurePosixPath(key)
        if (manifest_path.is_absolute() or ".." in manifest_path.parts
                or not key.startswith(prefix + "/")):
            raise HmR2BundleError("H&M R2 bundle manifest is outside the configured prefix.")
        # The dummy local path is never read by this adapter.
        super().__init__(Path(".hm-r2-not-materialized"), category_map, mapping_version,
                         usage_authorized=usage_authorized)
        self.store = store
        self.manifest_key = key
        self.allowed_prefix = prefix
        self._request_state = threading.local()

    @classmethod
    def from_env(cls, category_map, mapping_version: str, *, usage_authorized: bool,
                 store: R2ObjectStore | None = None) -> "R2HmHistoricalSalesSource":
        return cls(category_map, mapping_version, usage_authorized=usage_authorized, store=store)

    @contextmanager
    def _open_processed_directory(self):
        # This context manager is reached only after the parent has accepted an
        # request scope and category mapping are validated by the parent adapter.
        store = self.store or R2ObjectStore.from_env()
        try:
            bundle_bytes = store.get_bytes(self.manifest_key, max_bytes=8_000_000)
            bundle = json.loads(bundle_bytes)
        except R2ObjectError:
            raise HmR2BundleError("H&M R2 bundle manifest could not be read.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HmR2BundleError("H&M R2 bundle manifest is invalid.") from None
        if (bundle.get("schema_version") != "project07-hm-r2-processed-bundle/1.0"
                or bundle.get("source_id") != "hm-kaggle-personalized-fashion-recommendations"
                or bundle.get("data_nature") != "hm_kaggle_historical"):
            raise HmR2BundleError("H&M R2 bundle source identity or schema is invalid.")
        entries = bundle.get("files")
        if not isinstance(entries, list) or bundle.get("object_count") != len(entries):
            raise HmR2BundleError("H&M R2 bundle file inventory is invalid.")
        by_name: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise HmR2BundleError("H&M R2 bundle entry is invalid.")
            name, key = entry.get("name"), entry.get("object_key")
            if (name not in _REQUIRED_FILES or not isinstance(key, str)
                    or not key.startswith(self.allowed_prefix + "/")
                    or PurePosixPath(key).name != name or ".." in PurePosixPath(key).parts
                    or name in by_name):
                raise HmR2BundleError("H&M R2 bundle contains an invalid or duplicate object entry.")
            digest, size = entry.get("sha256"), entry.get("size_bytes")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or not isinstance(size, int) or isinstance(size, bool) or size < 0):
                raise HmR2BundleError("H&M R2 bundle integrity metadata is invalid.")
            if size > _MAX_HM_PROCESSED_OBJECT_BYTES:
                raise HmR2BundleError("H&M R2 processed object exceeds the configured size limit.")
            by_name[name] = entry
        if set(by_name) != _REQUIRED_FILES:
            raise HmR2BundleError("H&M R2 bundle is missing a required processed file.")
        if bundle.get("total_bytes") != sum(item["size_bytes"] for item in by_name.values()):
            raise HmR2BundleError("H&M R2 bundle byte inventory is invalid.")
        self._request_state.bundle_lineage = {
            "manifest_key": self.manifest_key,
            "manifest_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
            "schema_version": bundle["schema_version"],
            "files": by_name,
        }

        with tempfile.TemporaryDirectory(prefix="project07-hm-r2-") as temporary:
            directory = Path(temporary)
            for name in sorted(_REQUIRED_FILES):
                entry = by_name[name]
                try:
                    content = store.get_bytes(entry["object_key"], max_bytes=max(entry["size_bytes"], 1))
                except R2ObjectError:
                    raise HmR2BundleError("An H&M R2 processed object could not be read.") from None
                if len(content) != entry["size_bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                    raise HmR2BundleError("An H&M R2 processed object failed size/hash validation.")
                destination = directory / name
                temporary_path = directory / (name + ".part")
                temporary_path.write_bytes(content)
                try:
                    temporary_path.chmod(0o600)
                except OSError:
                    pass
                temporary_path.replace(destination)
            yield directory

    def _evidence_locator(self, source_type: str) -> str:
        name = "weekly_sales_by_article_channel.csv" if source_type == "historical_sales" else "article_catalog.csv"
        lineage = getattr(self._request_state, "bundle_lineage", {})
        entry = lineage.get("files", {}).get(name)
        if not entry:
            return super()._evidence_locator(source_type)
        return (f"r2://{entry['object_key']}#sha256={entry['sha256']}"
                f"&bundle_manifest={lineage.get('manifest_sha256', '')}")
