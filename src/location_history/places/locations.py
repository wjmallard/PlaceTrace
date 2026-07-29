"""
Load and save the date-ranged home/work location files.

Each entry's start_date/end_date may be null in the JSON, meaning the range
is open-ended on that side ("since forever" / "until further notice").
"""

import json
from datetime import date

from location_history.config import project_root


def locations_path(location_type):
    """Path of the JSON file for a location type ('home' or 'work')."""
    return project_root / "data" / f"{location_type}_locations.json"


def load_locations(location_type):
    """
    Load home or work locations with dates parsed to date objects.
    Open-ended range sides come back as None. Missing file loads as empty.
    """
    path = locations_path(location_type)
    if not path.exists():
        return []

    with open(path) as f:
        entries = json.load(f)

    for entry in entries:
        entry['start_date'] = date.fromisoformat(entry['start_date']) if entry['start_date'] else None
        entry['end_date'] = date.fromisoformat(entry['end_date']) if entry['end_date'] else None

    return entries


def save_locations(location_type, locations):
    """Save locations, serializing open-ended range sides as null."""
    data = []
    for loc in locations:
        loc_copy = loc.copy()
        loc_copy['start_date'] = loc['start_date'].isoformat() if loc['start_date'] else None
        loc_copy['end_date'] = loc['end_date'].isoformat() if loc['end_date'] else None
        data.append(loc_copy)

    with open(locations_path(location_type), 'w') as f:
        json.dump(data, f, indent=2)


def covers(entry, day):
    """True if the entry's date range contains the given date."""
    if entry['start_date'] and day < entry['start_date']:
        return False
    if entry['end_date'] and day > entry['end_date']:
        return False
    return True


def ranges_overlap(a, b):
    """True if two entries' date ranges overlap (None = unbounded side)."""
    a_start = a['start_date'] or date.min
    a_end = a['end_date'] or date.max
    b_start = b['start_date'] or date.min
    b_end = b['end_date'] or date.max
    return a_start <= b_end and b_start <= a_end
