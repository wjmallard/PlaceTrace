"""
pt-import-arc — Import Arc app daily exports.

Arc daily JSON exports (Export/JSON/Daily/*.json.gz) hold interleaved visits
and activities with dense GPS samples. This importer:

- Imports activities into Movements with source='arc' and full route geometry
- Labels existing visits with Arc's (mostly hand-corrected) place names

The Arc dataset is a closed collection period recorded alongside Google
Timeline, so the import is all-or-nothing per run: existing Arc movements are
kept unless --force re-imports them. Place labeling is idempotent.
"""

import argparse
import gzip
import json
import sys
import traceback
from datetime import timedelta, timezone

from tqdm import tqdm

from placetrace.config import ARC_EXPORT_DIR
from placetrace.db import get_main_connection
from placetrace.pipeline.import_movements import (
    find_adjacent_visit,
    insert_movement,
    normalize_activity_type,
)
from placetrace.pipeline.parse import parse_timestamp

# Arc activity types with a Google Timeline equivalent; anything else is
# normalized from Arc's own name (e.g. 'hiking' -> 'HIKING')
ARC_ACTIVITY_TYPES = {
    'airplane': 'FLYING',
    'boat': 'BOATING',
    'bus': 'IN_BUS',
    'car': 'IN_PASSENGER_VEHICLE',
    'motorcycle': 'MOTORCYCLING',
    'train': 'IN_TRAIN',
    'tram': 'IN_TRAM',
}

# Only label a visit with an Arc place if it is within this distance of the
# Arc visit's center
PLACE_MATCH_RADIUS_M = 250


def daily_files():
    """The Arc daily export files, oldest first."""
    return sorted(ARC_EXPORT_DIR.glob('*.json.gz'))


def load_daily(path):
    """Load one gzipped daily export's timeline items."""
    with gzip.open(path) as f:
        return json.load(f).get('timelineItems', [])


def sample_points(item):
    """(lon, lat) pairs for an item's located samples, in order."""
    points = []
    for sample in item.get('samples', []):
        location = sample.get('location')
        if location and location.get('latitude') is not None:
            points.append((location['longitude'], location['latitude']))
    return points


def local_parts(dt, item):
    """
    Wall-clock (date, time) for a UTC datetime, using the item's sample
    timezone offsets (falls back to UTC when no sample carries one).
    """
    for sample in item.get('samples', []):
        seconds = sample.get('secondsFromGMT')
        if seconds is not None:
            local = dt.astimezone(timezone(timedelta(seconds=seconds)))
            return local.date(), local.time()
    return dt.date(), dt.time()


def parse_arc_activity(item):
    """Parse one Arc activity item into a movement dict, or None if unusable."""
    try:
        start_time = parse_timestamp(item['startTime'] if 'startTime' in item else item['startDate'])
        end_time = parse_timestamp(item['endTime'] if 'endTime' in item else item['endDate'])
    except (KeyError, ValueError):
        return None

    points = sample_points(item)
    if not points:
        return None  # Nowhere to place it

    local_start_date, local_start_time = local_parts(start_time, item)
    local_end_date, local_end_time = local_parts(end_time, item)

    duration_minutes = max(1, round((end_time - start_time).total_seconds() / 60))

    raw_type = item.get('activityType')
    if raw_type:
        activity_type = ARC_ACTIVITY_TYPES.get(raw_type, normalize_activity_type(raw_type))
    else:
        activity_type = 'UNKNOWN'

    route_geometry = None
    if len(points) >= 2:
        route_geometry = f"LINESTRING({', '.join(f'{lon} {lat}' for lon, lat in points)})"

    start_lon, start_lat = points[0]
    end_lon, end_lat = points[-1]

    return {
        'start_time': start_time,
        'end_time': end_time,
        'duration_minutes': duration_minutes,
        'local_start_date': local_start_date,
        'local_start_time': local_start_time,
        'local_end_date': local_end_date,
        'local_end_time': local_end_time,
        'start_location': (start_lat, start_lon),
        'end_location': (end_lat, end_lon),
        'activity_type': activity_type,
        'confidence': item.get('activityTypeConfidenceScore'),
        'distance_meters': None,  # Calculated from route geometry after insert
        'source': 'arc',
        'movement_type': 'activity',
        'route_geometry': route_geometry,
        'source_metadata': {
            'format': 'arc_daily',
            'item_id': item.get('itemId'),
            'arc_activity_type': raw_type,
            'manual_activity_type': item.get('manualActivityType'),
            'uncertain_activity_type': item.get('uncertainActivityType'),
            'sample_count': len(item.get('samples', [])),
        },
    }


def import_arc_movements(conn, force=False):
    """Import Arc activities into Movements. Returns the imported count."""
    cursor = conn.cursor()

    cursor.execute("SELECT count(*) FROM Movements WHERE source = 'arc'")
    existing_count = cursor.fetchone()['count']

    if existing_count > 0:
        if force:
            print(f"⚠ Deleting {existing_count:,} existing Arc movements (--force)")
            cursor.execute("DELETE FROM Movements WHERE source = 'arc'")
            conn.commit()
        else:
            print(f"✓ {existing_count:,} Arc movements already imported, skipping")
            print("  To re-import, run: pt-import-arc --force")
            cursor.close()
            return 0

    imported = 0
    skipped = 0
    linked = 0

    for path in tqdm(daily_files(), desc="Importing Arc days"):
        for item in load_daily(path):
            if item.get('isVisit'):
                continue

            movement = parse_arc_activity(item)
            if not movement:
                skipped += 1
                continue

            preceding_visit_id = find_adjacent_visit(
                conn,
                movement['start_time'],
                movement['start_location'],
                is_start=True,
            )
            following_visit_id = find_adjacent_visit(
                conn,
                movement['end_time'],
                movement['end_location'],
                is_start=False,
            )
            if preceding_visit_id or following_visit_id:
                linked += 1

            insert_movement(cursor, movement, preceding_visit_id, following_visit_id)
            imported += 1

        conn.commit()

    # Calculate distances from route geometry
    cursor.execute("""
        UPDATE Movements
        SET distance_meters = ST_Length(route_geometry::geography)
        WHERE source = 'arc'
          AND route_geometry IS NOT NULL
          AND distance_meters IS NULL
    """)
    conn.commit()
    cursor.close()

    print(f"✓ Imported {imported:,} Arc movements ({skipped} without usable samples)")
    if imported > 0:
        print(f"  {linked:,} linked to adjacent visits ({100 * linked / imported:.0f}%)")

    return imported


def label_visits_with_places(conn):
    """
    Copy Arc place names onto overlapping visits.

    A visit gets the Arc name when it overlaps the Arc visit in time and lies
    within PLACE_MATCH_RADIUS_M of its center. Idempotent: re-running just
    rewrites the same names.
    """
    cursor = conn.cursor()
    arc_visits = 0
    named = 0
    labeled = 0

    for path in tqdm(daily_files(), desc="Labeling places"):
        for item in load_daily(path):
            if not item.get('isVisit'):
                continue
            arc_visits += 1

            place = item.get('place') or {}
            name = place.get('name') or item.get('streetAddress')
            center = item.get('center') or {}
            if not name or center.get('latitude') is None:
                continue
            named += 1

            cursor.execute("""
                UPDATE Visits
                SET place_name = %(name)s
                WHERE start_time < %(arc_end)s
                  AND end_time > %(arc_start)s
                  AND ST_DWithin(
                      location,
                      ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
                      %(radius_m)s
                  )
            """, {
                'name': name,
                'arc_start': parse_timestamp(item['startDate']),
                'arc_end': parse_timestamp(item['endDate']),
                'lat': center['latitude'],
                'lon': center['longitude'],
                'radius_m': PLACE_MATCH_RADIUS_M,
            })
            labeled += cursor.rowcount

        conn.commit()

    cursor.close()

    print(f"✓ Labeled {labeled:,} visits from {named:,} named Arc visits ({arc_visits:,} total)")

    return labeled


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import Arc app daily exports.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete existing Arc movements and re-import",
    )
    args = parser.parse_args(argv)

    if ARC_EXPORT_DIR is None:
        print("✗ No arc_export_dir configured under source_data in config.yaml")
        sys.exit(1)
    if not ARC_EXPORT_DIR.exists():
        print(f"✗ Arc export directory not found: {ARC_EXPORT_DIR}")
        sys.exit(1)

    files = daily_files()
    print("="*60)
    print("ARC IMPORT")
    print("="*60)
    print(f"\n{len(files)} daily exports: {files[0].name[:10]} -> {files[-1].name[:10]}\n")

    conn = get_main_connection()

    try:
        import_arc_movements(conn, force=args.force)
        label_visits_with_places(conn)
    except Exception as e:
        print(f"\n✗ Error during Arc import: {e}", file=sys.stderr)
        traceback.print_exc()
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
