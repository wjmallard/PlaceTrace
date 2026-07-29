"""Unified config loader for location-history."""

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


def _load_config(root):
    """Load config.yaml from the project root."""
    config_path = root / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


project_root = _find_project_root()
_config = _load_config(project_root)

MAIN_DB = _config["databases"]["main"]
OSM_DB = _config["databases"]["osm"]

LOCATION_HISTORY_JSON = Path(_config["source_data"]["location_history_json"]).expanduser()

_arc_export_dir = _config["source_data"].get("arc_export_dir")
ARC_EXPORT_DIR = Path(_arc_export_dir).expanduser() if _arc_export_dir else None

TRIPS = _config["trips"]
TRIP_CATEGORIES = _config["trips"]["categories"]

NUM_WORKERS = _config["processing"].get("num_workers", 4)

MAP = _config["map"]
