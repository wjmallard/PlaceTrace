"""Parsers for Google Timeline JSON fields, shared across import steps."""

from datetime import datetime, timezone


def parse_timestamp(timestamp_str):
    """Parse an ISO 8601 timestamp (offset or 'Z' suffix) and convert to UTC."""
    return datetime.fromisoformat(timestamp_str).astimezone(timezone.utc)


def local_date_time(timestamp_str):
    """
    Wall-clock (date, time) at the timestamp's own UTC offset.

    Example:
        '2024-05-18T07:54:00.030+02:00' -> (date(2024, 5, 18), time(7, 54, 0, 30000))
    """
    dt = datetime.fromisoformat(timestamp_str)
    return dt.date(), dt.time()


def explicit_offset(timestamp_str):
    """
    The timestamp's UTC offset as a timezone object, or None when the
    timestamp is bare UTC ('Z' suffix) and the real local zone is unknown.
    """
    if timestamp_str.endswith('Z'):
        return None
    dt = datetime.fromisoformat(timestamp_str)
    if dt.tzinfo is None:
        return None
    return timezone(dt.utcoffset())


def parse_geo_point(geo_str):
    """Parse 'geo:lat,lon' to a (lat, lon) tuple, or None if malformed."""
    if not geo_str or not geo_str.startswith('geo:'):
        return None

    coords = geo_str[4:].split(',')
    if len(coords) != 2:
        return None

    try:
        return float(coords[0]), float(coords[1])
    except ValueError:
        return None


def parse_latlng_e7(point_dict):
    """Parse {'latE7': ..., 'lngE7': ...} (degrees * 1e7) to a (lat, lon) tuple."""
    if 'latE7' not in point_dict or 'lngE7' not in point_dict:
        return None

    return point_dict['latE7'] / 1e7, point_dict['lngE7'] / 1e7
