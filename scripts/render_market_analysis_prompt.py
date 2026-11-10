#!/usr/bin/env python3
"""Render the review-only system prompt from its versioned source file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "prompts" / "manifest.json"


def render(prompt_id: str, version: str | None = None) -> Path:
    registry = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prompt = registry.get("prompts", {}).get(prompt_id)
    if not isinstance(prompt, dict):
        raise ValueError(f"Unknown prompt ID: {prompt_id}")
    version = version or prompt.get("current_version")
    versions = prompt.get("versions", {})
    entry = versions.get(version)
    if not isinstance(entry, dict):
        raise ValueError(f"Unregistered prompt version: {version}")
    source = ROOT / "prompts" / entry["system_template"]
    target = ROOT / "prompts" / entry["rendered_system"]
    content = source.read_text(encoding="utf-8")
    if "{{" in content or "}}" in content:
        raise ValueError("System template contains unresolved placeholders; renderer has no approved variable bindings.")
    target.write_text(content.rstrip() + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True, help="Prompt ID from prompts/manifest.json")
    parser.add_argument("--version", help="Version to render; defaults to the prompt's current_version")
    args = parser.parse_args()
    print(render(args.prompt, args.version).relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
