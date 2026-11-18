"""Central manifest of the R2 object keys."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ...resource_paths import CONFIGS_DIR


_DEFAULT_MANIFEST = CONFIGS_DIR / "r2-source-manifest.json"


def r2_source_key(name: str, *, default: str) -> str:
    """Resolve an R2 object key from the shared manifest or its default.

    A manifest path set in the environment may be absolute or relative to the
    current working directory.
    """
    manifest_value = os.getenv("RESEARCH_R2_SOURCE_MANIFEST", "").strip()
    manifest_path = Path(manifest_value).resolve() if manifest_value else _DEFAULT_MANIFEST
    try:
        payload: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if manifest_value:
            raise ValueError("The configured R2 source manifest does not exist.") from None
        payload = None
    except (OSError, json.JSONDecodeError):
        raise ValueError("The configured R2 source manifest is invalid or unreadable.") from None
    if payload is not None:
        if (not isinstance(payload, dict) or payload.get("schema") != "r2-source-manifest"
                or payload.get("schema_version") != "1.0.0"):
            raise ValueError("The configured R2 source manifest has an unsupported schema.")
        objects = payload.get("objects")
        if not isinstance(objects, dict):
            raise ValueError("The configured R2 source manifest has no objects mapping.")
        value = objects.get(name)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"R2 manifest object key {name!r} must be a non-empty string.")
            return value.strip()
    return default
