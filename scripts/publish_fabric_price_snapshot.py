"""Publish the reviewed public retail listing snapshot to an immutable R2 key.

Run from the repository root after reviewing the CSV source URLs and terms:
    .venv/bin/python scripts/publish_fabric_price_snapshot.py

The script reads R2 credentials from the process environment or the local
.env file, never prints them, and updates the selected manifest only after the
uploaded object has been verified.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/reference/fabric-prices/fabric_price_list.csv"
DEFAULT_MANIFEST = ROOT / "configs/r2-source-manifest.v1.json"
OBJECT_KEY = "example/fabric-prices/fabric_price_list.csv"


def _load_local_environment() -> None:
    """Read simple KEY=VALUE entries without emitting any values."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name.startswith(("R2_", "PROJECT07_R2_")):
            os.environ.setdefault(name, value)


def _selected_manifest() -> Path:
    configured = os.getenv("PROJECT07_R2_SOURCE_MANIFEST", "").strip()
    if not configured:
        return DEFAULT_MANIFEST
    path = Path(configured)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if ROOT not in path.parents and path != ROOT:
        raise ValueError("Configured manifest must be inside the project workspace.")
    if not path.is_file():
        raise FileNotFoundError("Configured R2 source manifest does not exist.")
    return path


def main() -> int:
    _load_local_environment()
    from market_research_agent.sources.market.fabric_price_r2_source import R2FabricPriceSource
    from market_research_agent.sources.shared.r2_object_store import R2ObjectStore

    payload = SOURCE.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    for material in ("cotton", "linen", "polyester", "wool", "모", "면", "리넨", "폴리에스테르"):
        R2FabricPriceSource._analyze_csv(
            payload, material, version="local-validation", sha256=digest,
            object_key=OBJECT_KEY,
        )

    manifest_path = _selected_manifest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "project07-r2-source-manifest/1.0":
        raise ValueError("Configured R2 source manifest has an unsupported schema.")
    objects = manifest.get("objects")
    if not isinstance(objects, dict) or not isinstance(objects.get("fabric_prices"), str):
        raise ValueError("Configured R2 source manifest has no fabric_prices object key.")

    store = R2ObjectStore.from_env()
    client = store._get_client()
    try:
        existing = client.head_object(Bucket=store.settings.bucket, Key=OBJECT_KEY)
    except Exception as exc:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode") if isinstance(response, dict) else None
        code = error.get("Code") if isinstance(error, dict) else None
        if status != 404 and code not in {"NoSuchKey", "NotFound", "404"}:
            raise RuntimeError(f"Cannot safely inspect target R2 object ({type(exc).__name__}).") from None
    else:
        metadata = existing.get("Metadata", {})
        if isinstance(metadata, dict) and metadata.get("sha256", "").casefold() == digest:
            print(f"Snapshot already present and verified: {OBJECT_KEY}")
            return 0
        raise FileExistsError("A different object already occupies the immutable snapshot key.")

    client.put_object(
        Bucket=store.settings.bucket,
        Key=OBJECT_KEY,
        Body=payload,
        ContentType="text/csv; charset=utf-8",
        Metadata={"sha256": digest, "schema-version": "fabric-price-list/1.0"},
        IfNoneMatch="*",
    )
    uploaded = client.head_object(Bucket=store.settings.bucket, Key=OBJECT_KEY)
    uploaded_metadata = uploaded.get("Metadata", {})
    if (int(uploaded.get("ContentLength", -1)) != len(payload)
            or not isinstance(uploaded_metadata, dict)
            or uploaded_metadata.get("sha256", "").casefold() != digest):
        raise RuntimeError("Uploaded R2 snapshot failed its size or SHA-256 verification.")

    objects["fabric_prices"] = OBJECT_KEY
    updated_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=manifest_path.parent, delete=False) as temporary:
        temp_path = Path(temporary.name)
        temporary.write(updated_manifest)
        temporary.flush()
        os.fsync(temporary.fileno())
    temp_path.replace(manifest_path)
    print(f"Published and verified {len(payload)} bytes at {OBJECT_KEY}")
    print(f"Updated fabric_prices in {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
