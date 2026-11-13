"""Central manifest for Project_07's R2 object keys."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_MANIFEST = _ROOT / "configs" / "r2-source-manifest.v1.json"


def r2_source_key(name: str, *, default: str, legacy_envs: tuple[str, ...] = ()) -> str:
    """Resolve an R2 object key from the shared manifest, with legacy fallback."""
    manifest_value = os.getenv("PROJECT07_R2_SOURCE_MANIFEST", "").strip()
    manifest_path = Path(manifest_value) if manifest_value else _DEFAULT_MANIFEST
    if not manifest_path.is_absolute():
        manifest_path = (_ROOT / manifest_path).resolve()
        if _ROOT not in manifest_path.parents and manifest_path != _ROOT:
            raise ValueError("PROJECT07_R2_SOURCE_MANIFEST must point inside the project workspace.")
    try:
        payload: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    except (OSError, json.JSONDecodeError):
        raise ValueError("The configured Project_07 R2 source manifest is invalid or unreadable.") from None
    if payload:
        if not isinstance(payload, dict) or payload.get("schema_version") != "project07-r2-source-manifest/1.0":
            raise ValueError("The configured Project_07 R2 source manifest has an unsupported schema.")
        objects = payload.get("objects")
        if not isinstance(objects, dict):
            raise ValueError("The configured Project_07 R2 source manifest has no objects mapping.")
        value = objects.get(name)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"R2 manifest object key {name!r} must be a non-empty string.")
            return value.strip()
    for env_name in legacy_envs:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return default
