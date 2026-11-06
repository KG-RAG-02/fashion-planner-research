"""Filesystem locations used by the local prototype."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "examples" / "fixtures" / "prototype_demo.json"
DEFAULT_DB_PATH = ROOT / "data" / "local" / "prototype.sqlite3"
DEFAULT_HM_ANALYSIS_PATH = (
    ROOT / "data" / "processed" / "hm-personalized-fashion-recommendations" / "hm_analysis.json"
)
IMAGEGEN_VARIANT_ID = "IMG-27SS-IMAGEGEN-01"
IMAGEGEN_ASSET = "/assets/27ss-light-utility-anorak-imagegen.png"


def database_path() -> Path:
    """Resolve the database path at call time so runtime configuration is honored."""

    return Path(os.getenv("VOGUE_DB_PATH", str(DEFAULT_DB_PATH)))


def hm_analysis_path() -> Path:
    """Resolve the optional Kaggle report path at call time."""

    return Path(os.getenv("VOGUE_HM_ANALYSIS_PATH", str(DEFAULT_HM_ANALYSIS_PATH)))


# Backwards-compatible import-time values; runtime code uses the resolvers above.
DB_PATH = database_path()
HM_ANALYSIS_PATH = hm_analysis_path()
