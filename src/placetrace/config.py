"""Unified config loader for PlaceTrace."""

import yaml
from pathlib import Path


def _find_project_root():
    """Walk up from this file to find the directory containing pyproject.toml."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise FileNotFoundError("Could not find pyproject.toml in any parent directory")


def _load_config():
    """Load config.yaml from the project root."""
    root = _find_project_root()
    config_path = root / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


config = _load_config()
