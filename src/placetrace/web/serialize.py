"""Row-to-JSON serializers shared by the API routes."""

# Visit columns with joined location fields; expects aliases v (Visits) and l (Locations)
VISIT_COLUMNS = """
    v.id,
    v.start_time,
    v.end_time,
    v.duration_minutes,
    v.local_start_date,
    v.local_start_time,
    v.local_end_date,
    v.local_end_time,
    ST_Y(v.location::geometry) AS latitude,
    ST_X(v.location::geometry) AS longitude,
    v.semantic_type,
    l.city,
    l.county,
    l.state,
    l.country
"""


def format_location_name(row):
    """
    Human-readable location name from joined Locations columns:
    city (or county if no city), then state, then country.
    Returns None if the row has no location (country is NULL).
    """
    if not row['country']:
        return None

    parts = []
    if row['city']:
        parts.append(row['city'])
    elif row['county']:
        parts.append(row['county'])
    if row['state']:
        parts.append(row['state'])
    parts.append(row['country'])

    return ", ".join(parts)


def visit_dict(row, include_local=True):
    """Serialize a visit row (with joined location columns) for the API."""
    visit = {
        'id': row['id'],
        'start_time': row['start_time'].isoformat() if row['start_time'] else None,
        'end_time': row['end_time'].isoformat() if row['end_time'] else None,
        'duration_minutes': row['duration_minutes'],
        'latitude': row['latitude'],
        'longitude': row['longitude'],
        'location_name': format_location_name(row),
        'semantic_type': row['semantic_type'],
    }

    if include_local:
        visit.update({
            'local_start_date': row['local_start_date'].isoformat() if row['local_start_date'] else None,
            'local_start_time': row['local_start_time'].isoformat() if row['local_start_time'] else None,
            'local_end_date': row['local_end_date'].isoformat() if row['local_end_date'] else None,
            'local_end_time': row['local_end_time'].isoformat() if row['local_end_time'] else None,
        })

    return visit
