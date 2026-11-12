"""Package assets shared by source, installed wheel and container runtimes."""
from importlib.resources import files
from pathlib import Path


def resource_root(name: str) -> Path:
    # setuptools installs these as regular package data in wheels/images.
    return Path(str(files('market_research_agent').joinpath('resources', name)))
