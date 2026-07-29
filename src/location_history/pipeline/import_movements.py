"""
pt-import-movements — Import movement data from Google Timeline JSON.

Handles multiple Google Timeline formats:
1. Old format (2014-2018): 'activity' objects
2. New format (2019+): 'activitySegment' objects
3. Standalone 'timelinePath' breadcrumb trails

All movements stored in Movements table with:
- source='google_timeline'
- movement_type='activity' or 'breadcrumb_trail'
- source_metadata JSONB for format-specific fields
"""

import argparse
import traceback
from bisect import bisect_left

import orjson
from datetime import timedelta
from tqdm import tqdm
import sys

from location_history.db import get_main_connection
from location_history.config import LOCATION_HISTORY_JSON
from location_history.pipeline.parse import (
    explicit_offset,
    local_date_time,
    parse_geo_point,
    parse_latlng_e7,
    parse_timestamp,
)

# Google revises recent segments between exports, so incremental imports
# re-import everything within this window before the newest stored movement
REVISION_WINDOW = timedelta(days=7)


def extract_start_end_local_times(start_time_str, end_time_str):
    """
    Extract local date/time for both start and end timestamps.
    Returns (start_date, start_time, end_date, end_time) tuple.
    """
    local_start_date, local_start_time = local_date_time(start_time_str)
    local_end_date, local_end_time = local_date_time(end_time_str)
    return local_start_date, local_start_time, local_end_date, local_end_time


def extract_route_geometry(activity):
    """
    Extract route geometry from timelinePath.waypoints.
    Returns WKT LINESTRING or None if no path data.
    """
    timeline_path = activity.get('timelinePath')
    if not timeline_path or 'waypoints' not in timeline_path:
        return None
    
    waypoints = timeline_path['waypoints']
    if not waypoints or len(waypoints) < 2:
        return None
    
    # Convert waypoints to (lon, lat) pairs for PostGIS LINESTRING
    coords = []
    for wp in waypoints:
        point = parse_latlng_e7(wp)
        if point:
            lat, lon = point
            coords.append(f"{lon} {lat}")  # PostGIS uses lon,lat order
    
    if len(coords) < 2:
        return None
    
    # Create WKT LINESTRING
    return f"LINESTRING({', '.join(coords)})"


def normalize_activity_type(activity_type):
    """
    Normalize activity type string to uppercase with underscores.
    Example: 'in passenger vehicle' -> 'IN_PASSENGER_VEHICLE'
    """
    if not activity_type:
        return 'UNKNOWN'
    
    return activity_type.upper().replace(' ', '_')


def find_adjacent_visit(conn, activity_time, location, is_start):
    """
    Find visit that temporally and spatially matches an activity endpoint.
    Uses PostGIS ST_DWithin for efficient spatial filtering.
    For an activity start, match the visit that ended just before it;
    for an activity end, match the visit that started just after it.
    """
    if not location:
        return None

    lat, lon = location
    cursor = conn.cursor()

    # Time window: 5 minutes before/after activity
    time_buffer = timedelta(minutes=5)
    time_column = 'end_time' if is_start else 'start_time'

    cursor.execute(f"""
        SELECT
            id,
            ST_Distance(
                location,
                ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
            ) as distance_m
        FROM Visits
        WHERE {time_column} BETWEEN %(window_start)s AND %(window_end)s
          AND ST_DWithin(
              location,
              ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
              500  -- 500m radius
          )
        ORDER BY ABS(EXTRACT(EPOCH FROM ({time_column} - %(activity_time)s))), distance_m
        LIMIT 1
    """, {
        'lat': lat,
        'lon': lon,
        'window_start': activity_time - time_buffer,
        'window_end': activity_time + time_buffer,
        'activity_time': activity_time,
    })

    result = cursor.fetchone()
    cursor.close()

    if result:
        if result['distance_m'] > 1000:
            print(f"  ⚠ Activity {'start' if is_start else 'end'} location {result['distance_m']:.0f}m from visit")
        return result['id']

    return None


def parse_movements_from_json(json_file_path):
    """
    Parse movements from location-history.json.
    Handles old format activities, new format activitySegments, and timelinePath breadcrumbs.
    Yields movement dicts with parsed timestamps and locations.
    """
    print(f"\nReading location history: {json_file_path}")
    
    with open(json_file_path, 'rb') as f:
        data = orjson.loads(f.read())
    
    # Handle both formats: list of objects or dict with timelineObjects key
    if isinstance(data, list):
        timeline_objects = data
    elif isinstance(data, dict):
        timeline_objects = data.get('timelineObjects', [])
    else:
        print("✗ Unexpected JSON format")
        return
    
    if not timeline_objects:
        print("✗ No timeline objects found in JSON")
        return
    
    print(f"✓ Found {len(timeline_objects):,} timeline objects")
    
    activity_count = 0
    breadcrumb_count = 0
    
    # Breadcrumb trail timestamps are bare UTC; visits and activities carry
    # real offsets, so each trail borrows the offset of the entry nearest
    # to it in time (file order is not reliably chronological)
    offsets = collect_offsets(timeline_objects)

    for obj in timeline_objects:
        # Skip visits (handled by other import script)
        if 'visit' in obj:
            continue

        # Parse standalone timelinePath breadcrumb trails
        if 'timelinePath' in obj and 'activity' not in obj and 'activitySegment' not in obj:
            movement = parse_breadcrumb_trail(obj, offsets)
            if movement:
                breadcrumb_count += 1
                yield movement
            continue
        
        # Parse activity (old format) or activitySegment (new format)
        if 'activity' in obj or 'activitySegment' in obj:
            key = 'activity' if 'activity' in obj else 'activitySegment'
            movement = parse_activity(obj, key)
            if movement:
                activity_count += 1
                yield movement
    
    print(f"✓ Parsed {activity_count:,} activity movements")
    print(f"✓ Parsed {breadcrumb_count:,} breadcrumb trails")


def parse_activity(obj, key):
    """
    Parse an activity object into a movement dict.

    Handles both formats:
    - key='activity': old format (2014-2018), no route data
    - key='activitySegment': new format (2019+), may include timelinePath,
      simplifiedRawPath, editConfirmationStatus, parkingEvent
    """
    activity = obj[key]

    # Parse timestamps (at top level)
    try:
        start_time_str = obj['startTime']
        end_time_str = obj['endTime']
        start_time = parse_timestamp(start_time_str)
        end_time = parse_timestamp(end_time_str)
    except (KeyError, ValueError):
        return None

    # Extract local date/time from both start and end timestamps
    local_start_date, local_start_time, local_end_date, local_end_time = extract_start_end_local_times(start_time_str, end_time_str)

    # Calculate duration (minimum 1 minute)
    duration_minutes = max(1, round((end_time - start_time).total_seconds() / 60))

    # Parse start/end locations
    start_location = parse_geo_point(activity.get('start'))
    end_location = parse_geo_point(activity.get('end'))

    if not start_location or not end_location:
        return None

    # Extract activity type and confidence
    top_candidate = activity.get('topCandidate', {})
    activity_type = normalize_activity_type(top_candidate.get('type', 'UNKNOWN'))
    confidence = float(top_candidate.get('probability', 0.0))

    distance_meters = float(activity.get('distanceMeters', 0.0))

    if key == 'activity':
        route_geometry = None  # Old format doesn't have routes
        source_metadata = {
            'format': 'old_activity',
            'top_candidate': top_candidate,
        }
    else:
        route_geometry = extract_route_geometry(activity)
        source_metadata = {
            'format': 'new_activity_segment',
            'top_candidate': top_candidate,
            'edit_confirmation_status': activity.get('editConfirmationStatus'),
            'parking_event': activity.get('parkingEvent'),
            'has_simplified_raw_path': 'simplifiedRawPath' in activity,
        }

    return {
        'start_time': start_time,
        'end_time': end_time,
        'duration_minutes': duration_minutes,
        'local_start_date': local_start_date,
        'local_start_time': local_start_time,
        'local_end_date': local_end_date,
        'local_end_time': local_end_time,
        'start_location': start_location,
        'end_location': end_location,
        'activity_type': activity_type,
        'confidence': confidence,
        'distance_meters': distance_meters,
        'source': 'google_timeline',
        'movement_type': 'activity',
        'route_geometry': route_geometry,
        'source_metadata': source_metadata
    }


def collect_offsets(timeline_objects):
    """
    (utc_time, timezone) for every entry carrying an explicit UTC offset,
    sorted by time. These reveal the local timezone at each moment.
    """
    offsets = []
    for obj in timeline_objects:
        ts = obj.get('startTime')
        if not ts:
            continue
        tz = explicit_offset(ts)
        if tz is not None:
            offsets.append((parse_timestamp(ts), tz))

    offsets.sort(key=lambda pair: pair[0])

    return offsets


def offset_near(offsets, when):
    """The offset of the entry temporally nearest to the given moment."""
    if not offsets:
        return None

    i = bisect_left(offsets, when, key=lambda pair: pair[0])
    candidates = [c for c in (i - 1, i) if 0 <= c < len(offsets)]
    nearest = min(candidates, key=lambda c: abs((offsets[c][0] - when).total_seconds()))

    return offsets[nearest][1]


def parse_breadcrumb_trail(obj, offsets=None):
    """
    Parse standalone timelinePath breadcrumb trail.

    Trail timestamps are bare UTC ('Z'), unlike visits and activities, so
    local wall-clock fields use the offset of the temporally nearest
    offset-carrying entry, when one is available.

    Structure:
    {
        "startTime": "...",
        "endTime": "...",
        "timelinePath": [
            {"point": "geo:lat,lon", "durationMinutesOffsetFromStartTime": "10"},
            ...
        ]
    }
    """
    try:
        start_time_str = obj['startTime']
        end_time_str = obj['endTime']
        start_time = parse_timestamp(start_time_str)
        end_time = parse_timestamp(end_time_str)
    except (KeyError, ValueError):
        return None

    # Extract local date/time from both start and end timestamps
    borrowed = offset_near(offsets, start_time) if offsets and start_time_str.endswith('Z') else None
    if borrowed is not None:
        local_start = start_time.astimezone(borrowed)
        local_end = end_time.astimezone(borrowed)
        local_start_date, local_start_time = local_start.date(), local_start.time()
        local_end_date, local_end_time = local_end.date(), local_end.time()
    else:
        local_start_date, local_start_time, local_end_date, local_end_time = extract_start_end_local_times(start_time_str, end_time_str)
    
    duration_minutes = max(1, round((end_time - start_time).total_seconds() / 60))
    
    timeline_path = obj.get('timelinePath', [])
    if not timeline_path or len(timeline_path) < 2:
        return None
    
    # Extract start and end locations from path
    first_point = parse_geo_point(timeline_path[0].get('point'))
    last_point = parse_geo_point(timeline_path[-1].get('point'))
    
    if not first_point or not last_point:
        return None
    
    # Build route geometry from breadcrumbs
    coords = []
    for waypoint in timeline_path:
        point = parse_geo_point(waypoint.get('point'))
        if point:
            lat, lon = point
            coords.append(f"{lon} {lat}")
    
    route_geometry = f"LINESTRING({', '.join(coords)})" if len(coords) >= 2 else None
    
    # Distance calculated by PostGIS from route_geometry during insert
    # We'll pass None here and let the database calculate it
    distance_meters = None
    
    source_metadata = {
        'format': 'standalone_timeline_path',
        'waypoint_count': len(timeline_path)
    }
    
    return {
        'start_time': start_time,
        'end_time': end_time,
        'duration_minutes': duration_minutes,
        'local_start_date': local_start_date,
        'local_start_time': local_start_time,
        'local_end_date': local_end_date,
        'local_end_time': local_end_time,
        'start_location': first_point,
        'end_location': last_point,
        'activity_type': None,  # Breadcrumbs don't have activity type
        'confidence': None,
        'distance_meters': distance_meters,
        'source': 'google_timeline',
        'movement_type': 'breadcrumb_trail',
        'route_geometry': route_geometry,
        'source_metadata': source_metadata
    }


def insert_movement(cursor, movement, preceding_visit_id, following_visit_id):
    """Insert one parsed movement dict into the Movements table."""
    start_lat, start_lon = movement['start_location']
    end_lat, end_lon = movement['end_location']
    source_metadata_json = orjson.dumps(movement['source_metadata']).decode('utf-8') if movement['source_metadata'] else None

    cursor.execute("""
        INSERT INTO Movements (
            start_time,
            end_time,
            duration_minutes,
            local_start_date,
            local_start_time,
            local_end_date,
            local_end_time,
            start_location,
            end_location,
            route_geometry,
            activity_type,
            confidence,
            distance_meters,
            source,
            movement_type,
            source_metadata,
            preceding_visit_id,
            following_visit_id
        ) VALUES (
            %(start_time)s,
            %(end_time)s,
            %(duration_minutes)s,
            %(local_start_date)s,
            %(local_start_time)s,
            %(local_end_date)s,
            %(local_end_time)s,
            ST_SetSRID(ST_MakePoint(%(start_lon)s, %(start_lat)s), 4326)::geography,
            ST_SetSRID(ST_MakePoint(%(end_lon)s, %(end_lat)s), 4326)::geography,
            ST_GeomFromText(%(route_geometry)s, 4326)::geography,
            %(activity_type)s,
            %(confidence)s,
            %(distance_meters)s,
            %(source)s,
            %(movement_type)s,
            %(source_metadata)s,
            %(preceding_visit_id)s,
            %(following_visit_id)s
        )
    """, {
        'start_time': movement['start_time'],
        'end_time': movement['end_time'],
        'duration_minutes': movement['duration_minutes'],
        'local_start_date': movement['local_start_date'],
        'local_start_time': movement['local_start_time'],
        'local_end_date': movement['local_end_date'],
        'local_end_time': movement['local_end_time'],
        'start_lat': start_lat,
        'start_lon': start_lon,
        'end_lat': end_lat,
        'end_lon': end_lon,
        'route_geometry': movement['route_geometry'],
        'activity_type': movement['activity_type'],
        'confidence': movement['confidence'],
        'distance_meters': movement['distance_meters'],
        'source': movement['source'],
        'movement_type': movement['movement_type'],
        'source_metadata': source_metadata_json,
        'preceding_visit_id': preceding_visit_id,
        'following_visit_id': following_visit_id,
    })


def import_movements_to_database(conn, movements, force=False):
    """
    Import movements into Movements table.
    Links to adjacent visits using temporal and spatial proximity.

    Incremental by default: only movements ending after the stored high-water
    mark (minus REVISION_WINDOW) are imported, replacing that window's stored
    rows. --force wipes and re-imports everything.
    """
    cursor = conn.cursor()

    cursor.execute("SELECT count(*) FROM Movements WHERE source = 'google_timeline'")
    existing_count = cursor.fetchone()['count']

    if force and existing_count > 0:
        print(f"\n⚠ Deleting {existing_count:,} existing movements (--force)")
        cursor.execute("DELETE FROM Movements WHERE source = 'google_timeline'")
        conn.commit()
    elif existing_count > 0:
        cursor.execute("SELECT max(end_time) AS high_water FROM Movements WHERE source = 'google_timeline'")
        cutoff = cursor.fetchone()['high_water'] - REVISION_WINDOW

        parsed_count = len(movements)
        movements = [m for m in movements if m['end_time'] > cutoff]

        cursor.execute("""
            DELETE FROM Movements
            WHERE source = 'google_timeline'
              AND end_time > %(cutoff)s
        """, {
            'cutoff': cutoff,
        })
        replaced = cursor.rowcount
        conn.commit()

        print(f"\nIncremental import of movements after {cutoff:%Y-%m-%d}")
        print(f"  {len(movements):,} of {parsed_count:,} parsed movements are new (replacing {replaced:,} stored in the revision window)")

        if not movements:
            print("✓ Nothing new to import")
            cursor.close()
            return 0

    print("\nImporting movements...")
    
    imported_count = 0
    skipped_count = 0
    linked_to_preceding = 0
    linked_to_following = 0
    
    try:
        for movement in tqdm(movements, desc="Importing movements"):
            # Find adjacent visits
            preceding_visit_id = find_adjacent_visit(
                conn, 
                movement['start_time'], 
                movement['start_location'],
                is_start=True
            )
            
            following_visit_id = find_adjacent_visit(
                conn,
                movement['end_time'],
                movement['end_location'],
                is_start=False
            )
            
            if preceding_visit_id:
                linked_to_preceding += 1
            if following_visit_id:
                linked_to_following += 1
            
            insert_movement(cursor, movement, preceding_visit_id, following_visit_id)

            imported_count += 1
            
            # Commit every 1000 records
            if imported_count % 1000 == 0:
                conn.commit()
        
        # Final commit
        conn.commit()
        
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user (Ctrl-C)")
        print("Committing current progress...")
        conn.commit()
        print(f"✓ Imported {imported_count:,} movements before interruption")
        cursor.close()
        return imported_count
    
    cursor.close()
    
    # Calculate distance for breadcrumb trails from route geometry using PostGIS
    print("\nCalculating distances for breadcrumb trails from route geometry...")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE Movements
        SET distance_meters = ST_Length(route_geometry::geography)
        WHERE movement_type = 'breadcrumb_trail'
          AND route_geometry IS NOT NULL
          AND distance_meters IS NULL
    """)
    updated_count = cursor.rowcount
    conn.commit()
    cursor.close()
    print(f"✓ Calculated distances for {updated_count:,} breadcrumb trails")
    
    print(f"\n✓ Imported {imported_count:,} movements")
    if imported_count > 0:
        print(f"  - {linked_to_preceding:,} linked to preceding visit ({100*linked_to_preceding/imported_count:.1f}%)")
        print(f"  - {linked_to_following:,} linked to following visit ({100*linked_to_following/imported_count:.1f}%)")
    
    return imported_count


def print_movement_summary(conn):
    """Print summary statistics about imported movements"""
    cursor = conn.cursor()
    
    # Total movements
    cursor.execute("SELECT COUNT(*) as count FROM Movements")
    total = cursor.fetchone()['count']
    
    # Movements by type
    cursor.execute("""
        SELECT 
            movement_type,
            COUNT(*) as count
        FROM Movements
        GROUP BY movement_type
        ORDER BY count DESC
    """)
    by_movement_type = cursor.fetchall()
    
    # Movements by activity type (for activities only)
    cursor.execute("""
        SELECT 
            activity_type,
            COUNT(*) as count,
            SUM(distance_meters) / 1000.0 as total_km,
            SUM(duration_minutes) / 60.0 as total_hours
        FROM Movements
        WHERE movement_type = 'activity' AND activity_type IS NOT NULL
        GROUP BY activity_type
        ORDER BY total_km DESC
    """)
    by_activity_type = cursor.fetchall()
    
    # Total distance and duration
    cursor.execute("""
        SELECT 
            SUM(distance_meters) / 1000.0 as total_km,
            SUM(duration_minutes) / 60.0 as total_hours
        FROM Movements
    """)
    totals = cursor.fetchone()
    
    # Movements with route geometry
    cursor.execute("""
        SELECT COUNT(*) as count 
        FROM Movements 
        WHERE route_geometry IS NOT NULL
    """)
    with_routes = cursor.fetchone()['count']
    
    cursor.close()
    
    print("\n" + "="*60)
    print("MOVEMENT IMPORT SUMMARY")
    print("="*60)
    
    print(f"\nTotal movements:     {total:>8,}")
    print(f"Total distance:      {totals['total_km']:>8,.1f} km")
    print(f"Total duration:      {totals['total_hours']:>8,.1f} hours")
    print(f"With route geometry: {with_routes:>8,} ({100*with_routes/total:.1f}%)")
    
    if by_movement_type:
        print(f"\nMovements by type:")
        for row in by_movement_type:
            print(f"  {row['movement_type']:<25} {row['count']:>6,}")
    
    if by_activity_type:
        print(f"\nActivities by type:")
        for row in by_activity_type:
            print(f"  {row['activity_type']:<25} {row['count']:>6,}  ({row['total_km']:>8,.1f} km, {row['total_hours']:>6,.1f} hrs)")
    
    print("="*60 + "\n")


def main(argv=None):
    """Main execution flow"""
    parser = argparse.ArgumentParser(description="Import movements from Google Timeline JSON.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete existing movements and re-import",
    )
    args = parser.parse_args(argv)

    print("="*60)
    print("MOVEMENT IMPORT (Google Timeline)")
    print("="*60)
    
    if not LOCATION_HISTORY_JSON.exists():
        print(f"\n✗ Location history file not found: {LOCATION_HISTORY_JSON}")
        sys.exit(1)
    
    # Connect to database
    conn = get_main_connection()
    
    try:
        # Parse movements from JSON (convert generator to list for progress bar)
        movements = list(parse_movements_from_json(LOCATION_HISTORY_JSON))
        
        if not movements:
            print("\n✓ No movements found to import")
            return
        
        print(f"✓ Total movements to import: {len(movements):,}")
        
        # Import to database
        imported = import_movements_to_database(conn, movements, force=args.force)

        if imported > 0:
            # Print summary
            print_movement_summary(conn)
        
        print("✓ Movement import complete!")
        
    except Exception as e:
        print(f"\n✗ Error during import: {e}", file=sys.stderr)
        traceback.print_exc()
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
