"""Load the versioned synthetic R2 release into the existing validated RAG bundle."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from ...synthetic_rag import SYNTHETIC_MARKERS, SyntheticFixtureError, load_fixture_bundle
from ..shared.r2_object_store import R2ObjectError, R2ObjectStore

DEFAULT_SYNTHETIC_R2_MANIFEST_KEY = (
    "example/synthetic-release/manifest.json"
)
_RUNTIME_GROUPS = {
    "runtime/query_coverage": "query_coverage",
    "runtime/market_scenario": "market_scenario",
}
_MAX_SYNTHETIC_RUNTIME_OBJECT_BYTES = 64_000_000


class SyntheticR2BundleError(SyntheticFixtureError):
    """The shared synthetic release is missing or invalid."""


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise SyntheticR2BundleError("Synthetic R2 manifest contains an unsafe relative path.")
    return path


class SyntheticR2FixtureBundleStore:
    """Fetch only allowlisted runtime groups; evaluation objects are never loaded."""

    def __init__(self, store: R2ObjectStore, manifest_key: str = DEFAULT_SYNTHETIC_R2_MANIFEST_KEY) -> None:
        self.store = store
        self.manifest_key = manifest_key
        root = PurePosixPath(manifest_key)
        if (root.name != "manifest.json" or root.is_absolute() or ".." in root.parts
                or not manifest_key.startswith("example/synthetic-release/manifest.json")):
            raise SyntheticR2BundleError("Synthetic R2 release manifest key is not allowlisted.")
        self.release_root = str(root.parent)

    @classmethod
    def from_env(cls, *, store: R2ObjectStore | None = None) -> "SyntheticR2FixtureBundleStore":
        manifest_key = os.getenv("PROJECT07_SYNTHETIC_R2_MANIFEST_KEY", "").strip()
        release_prefix = os.getenv("PROJECT07_SYNTHETIC_R2_RELEASE_PREFIX", "").strip().strip("/")
        if manifest_key and release_prefix:
            raise SyntheticR2BundleError("Configure either the R2 manifest key or release prefix, not both.")
        if release_prefix:
            manifest_key = f"{release_prefix}/manifest.json"
        return cls(store or R2ObjectStore.from_env(), manifest_key or DEFAULT_SYNTHETIC_R2_MANIFEST_KEY)

    def load_bundle(self) -> dict[str, Any]:
        try:
            manifest_bytes, manifest_metadata = self.store.get_bytes_and_metadata(self.manifest_key, max_bytes=16_000_000)
            manifest = json.loads(manifest_bytes)
        except (R2ObjectError, ValueError, TypeError):
            raise SyntheticR2BundleError("Synthetic R2 release manifest could not be read.") from None
        if not self._has_safety_metadata(manifest_metadata):
            raise SyntheticR2BundleError("Synthetic R2 manifest object metadata is missing required safety markers.")
        if not isinstance(manifest, dict):
            raise SyntheticR2BundleError("Synthetic R2 release manifest is invalid.")
        if (manifest.get("schema_version") != "project07-synthetic-r2-bundle/1.0"
                or any(manifest.get(key) != expected for key, expected in SYNTHETIC_MARKERS.items())
                or manifest.get("release_id") != PurePosixPath(self.release_root).name):
            raise SyntheticR2BundleError("Synthetic R2 release marker or version validation failed.")
        entries = manifest.get("objects")
        if not isinstance(entries, list) or manifest.get("object_count") != len(entries):
            raise SyntheticR2BundleError("Synthetic R2 release object inventory is invalid.")
        if any(not isinstance(item, dict) for item in entries):
            raise SyntheticR2BundleError("Synthetic R2 release object entry is invalid.")
        declared_sizes = [item.get("size_bytes") for item in entries]
        if any(not isinstance(size, int) or isinstance(size, bool) or size < 0 for size in declared_sizes):
            raise SyntheticR2BundleError("Synthetic R2 release object size is invalid.")
        if manifest.get("total_bytes") != sum(declared_sizes):
            raise SyntheticR2BundleError("Synthetic R2 release byte inventory is invalid.")

        selected: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in entries:
            group = entry.get("dataset_group")
            if group not in _RUNTIME_GROUPS:
                # Evaluation and example request groups are intentionally not runtime data.
                continue
            object_key = entry.get("object_key")
            relative = entry.get("relative_path")
            key_path = PurePosixPath(object_key) if isinstance(object_key, str) else PurePosixPath("invalid")
            if (not isinstance(object_key, str) or not object_key.startswith(self.release_root + "/")
                    or key_path.is_absolute() or ".." in key_path.parts or not isinstance(relative, str)):
                raise SyntheticR2BundleError("Synthetic R2 runtime object is outside the declared release.")
            relative_path = _safe_relative(relative)
            identity = (group, relative_path.as_posix())
            if identity in selected:
                raise SyntheticR2BundleError("Synthetic R2 release contains a duplicate runtime path.")
            digest = entry.get("sha256")
            size = entry.get("size_bytes")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or not isinstance(size, int) or size < 0):
                raise SyntheticR2BundleError("Synthetic R2 object integrity metadata is invalid.")
            if size > _MAX_SYNTHETIC_RUNTIME_OBJECT_BYTES:
                raise SyntheticR2BundleError("Synthetic R2 runtime object exceeds the configured size limit.")
            selected[identity] = entry
        if not any(group == "runtime/query_coverage" for group, _ in selected):
            raise SyntheticR2BundleError("Synthetic R2 release has no runtime query coverage data.")
        if not any(group == "runtime/market_scenario" for group, _ in selected):
            raise SyntheticR2BundleError("Synthetic R2 release has no runtime explanatory documents.")

        # Materialize into a per-request temporary root only after validating the allowlisted manifest.
        with tempfile.TemporaryDirectory(prefix="project07-synthetic-r2-") as temp_name:
            root = Path(temp_name)
            coverage_root = root / "query_coverage"
            scenario_root = root / "market_scenario"
            for (group, relative), entry in sorted(selected.items()):
                destination_root = coverage_root if group == "runtime/query_coverage" else scenario_root
                destination = destination_root / Path(*_safe_relative(relative).parts)
                try:
                    content, object_metadata = self.store.get_bytes_and_metadata(
                        entry["object_key"], max_bytes=max(entry["size_bytes"], 1),
                    )
                except R2ObjectError as exc:
                    raise SyntheticR2BundleError("A required synthetic R2 runtime object could not be read.") from None
                if (not self._has_safety_metadata(object_metadata)
                        or len(content) != entry["size_bytes"]
                        or hashlib.sha256(content).hexdigest() != entry["sha256"]):
                    raise SyntheticR2BundleError("A required synthetic R2 runtime object failed size/hash validation.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".part")
                temporary.write_bytes(content)
                try:
                    temporary.chmod(0o600)
                except OSError:
                    pass
                temporary.replace(destination)
            try:
                bundle = load_fixture_bundle(
                    coverage_root, scenario_root,
                    trusted_external_roots=(coverage_root, scenario_root),
                )
            except (OSError, ValueError, TypeError):
                raise SyntheticR2BundleError("Synthetic R2 runtime fixtures failed source validation.") from None
            bundle["_r2_release"] = {
                "schema_version": manifest["schema_version"],
                "release_id": manifest["release_id"],
                "source_commit": manifest.get("source_commit"),
                "manifest_key": self.manifest_key,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "runtime_objects": [
                    {key: entry.get(key) for key in ("dataset_group", "relative_path", "object_key", "size_bytes", "sha256")}
                    for (_group, _relative), entry in sorted(selected.items())
                ],
            }
            return bundle

    @staticmethod
    def _has_safety_metadata(metadata: dict[str, str]) -> bool:
        expected = {
            "synthetic": "true", "data_nature": "synthetic_test",
            "permission_status": "authorized_for_synthetic_testing_only",
            "operational_eligible": "false",
        }
        return all(metadata.get(key) == value for key, value in expected.items())
