#!/usr/bin/env python3
"""
5b_import_movements.py

Parse and import movement data from Google Timeline JSON into Movements table.

Handles multiple Google Timeline formats:
1. Old format (2014-2018): 'activity' objects
2. New format (2019+): 'activitySegment' objects  
3. Standalone 'timelinePath' breadcrumb trails

All movements stored in unified Movements table with:
- source='google_timeline'
- movement_type='activity' or 'breadcrumb_trail'
- source_metadata JSONB for format-specific fields

Run this after 2_import_visits.py and before 6_detect_trips.py

Usage:
    python 5b_import_movements.py
"""

import orjson
from pathlib import Path
from datetime import datetime, timezone, timedelta
from tqdm import tqdm
import sys

# Import database module
from db import get_unified_connection


def load_config():
    """Load configuration from config.yaml"""
    import yaml
    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def parse_timestamp(timestamp_str):
    """
    Parse Google Timeline timestamp string to datetime with timezone.
    Example: '2024-05-18T06:09:05.023+02:00'
    """
    return datetime.fromisoformat(timestamp_str)


def parse_geo_point(geo_str):
    """
    Parse 'geo:lat,lon' string to (lat, lon) tuple.
    Example: 'geo:48.636104,-1.511244' -> (48.636104, -1.511244)
    """
    if not geo_str or not geo_str.startswith('geo:'):
        return None
    
    coords = geo_str[4:].split(',')
    if len(coords) != 2:
        return None
    
    try:
        lat = float(coords[0])
        lon = float(coords[1])
        return (lat, lon)
    except ValueError:
        return None


def parse_latlng_e7(point_dict):
    """
    Parse latE7/lngE7 format (integer degrees * 1e7) to (lat, lon) tuple.
    Example: {'latE7': 486361040, 'lngE7': -15112440} -> (48.6361040, -1.5112440)
    """
    if 'latE7' not in point_dict or 'lngE7' not in point_dict:
        return None
    
    lat = point_dict['latE7'] / 1e7
    lon = point_dict['lngE7'] / 1e7
    return (lat, lon)


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


def extract_raw_path(activity):
    """
    Extract sparse GPS pings from simplifiedRawPath.
    Returns JSONB-compatible dict or None.
    """
    raw_path = activity.get('simplifiedRawPath')
    if not raw_path or 'points' not in raw_path:
        return None
    
    return raw_path['points']


def extract_parking_event(activity):
    """
    Extract parking event metadata if present.
    Returns JSONB-compatible dict or None.
    """
    return activity.get('parkingEvent')


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
    """
    if not location:
        return None
    
    lat, lon = location
    cursor = conn.cursor()
    
    # Time window: 5 minutes before/after activity
    time_buffer = timedelta(minutes=5)
    
    if is_start:
        # Visit that ended just before activity started
        cursor.execute("""
            SELECT id,
                   ST_Distance(
                       location,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                   ) as distance_m
            FROM Visits
            WHERE end_time BETWEEN %s AND %s
              AND ST_DWithin(
                  location,
                  ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                  500  -- 500m radius
              )
            ORDER BY ABS(EXTRACT(EPOCH FROM (end_time - %s))), distance_m
            LIMIT 1
        """, (
            lon, lat,  # Point for distance calculation
            activity_time - time_buffer,
            activity_time + time_buffer,
            lon, lat,  # Point for ST_DWithin
            activity_time
        ))
    else:
        # Visit that started just after activity ended
        cursor.execute("""
            SELECT id,
                   ST_Distance(
                       location,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                   ) as distance_m
            FROM Visits
            WHERE start_time BETWEEN %s AND %s
              AND ST_DWithin(
                  location,
                  ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                  500
              )
            ORDER BY ABS(EXTRACT(EPOCH FROM (start_time - %s))), distance_m
            LIMIT 1
        """, (
            lon, lat,
            activity_time - time_buffer,
            activity_time + time_buffer,
            lon, lat,
            activity_time
        ))
    
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
    
    for obj in timeline_objects:
        # Skip visits (handled by other import script)
        if 'visit' in obj:
            continue
        
        # Parse standalone timelinePath breadcrumb trails
        if 'timelinePath' in obj and 'activity' not in obj and 'activitySegment' not in obj:
            movement = parse_breadcrumb_trail(obj)
            if movement:
                breadcrumb_count += 1
                yield movement
            continue
        
        # Parse activity (old format) or activitySegment (new format)
        if 'activity' in obj:
            movement = parse_activity_old_format(obj)
            if movement:
                activity_count += 1
                yield movement
        elif 'activitySegment' in obj:
            movement = parse_activity_new_format(obj)
            if movement:
                activity_count += 1
                yield movement
    
    print(f"✓ Parsed {activity_count:,} activity movements")
    print(f"✓ Parsed {breadcrumb_count:,} breadcrumb trails")


def parse_activity_old_format(obj):
    """
    Parse old format activity object (2014-2018).
    
    Structure:
    {
        "startTime": "...",
        "endTime": "...",
        "activity": {
            "start": "geo:lat,lon",
            "end": "geo:lat,lon",
            "topCandidate": {"type": "walking", "probability": "0.5"},
            "distanceMeters": "1234.5"
        }
    }
    """
    activity = obj['activity']
    
    # Parse timestamps (at top level)
    try:
        start_time = parse_timestamp(obj['startTime'])
        end_time = parse_timestamp(obj['endTime'])
    except (KeyError, ValueError) as e:
        return None
    
    # Calculate duration
    duration_minutes = int((end_time - start_time).total_seconds() / 60)
    
    # Parse start/end locations
    start_location = parse_geo_point(activity.get('start'))
    end_location = parse_geo_point(activity.get('end'))
    
    if not start_location or not end_location:
        return None
    
    # Extract activity type and confidence
    top_candidate = activity.get('topCandidate', {})
    activity_type = normalize_activity_type(top_candidate.get('type', 'UNKNOWN'))
    confidence = float(top_candidate.get('probability', 0.0))
    
    # Extract distance
    distance_meters = float(activity.get('distanceMeters', 0.0))
    
    # Build source metadata
    source_metadata = {
        'format': 'old_activity',
        'top_candidate': top_candidate
    }
    
    return {
        'start_time': start_time,
        'end_time': end_time,
        'duration_minutes': duration_minutes,
        'start_location': start_location,
        'end_location': end_location,
        'activity_type': activity_type,
        'confidence': confidence,
        'distance_meters': distance_meters,
        'source': 'google_timeline',
        'movement_type': 'activity',
        'route_geometry': None,  # Old format doesn't have routes
        'source_metadata': source_metadata
    }


def parse_activity_new_format(obj):
    """
    Parse new format activitySegment object (2019+).
    
    Structure similar to old format but may have additional fields like
    timelinePath, simplifiedRawPath, editConfirmationStatus, parkingEvent.
    """
    activity = obj['activitySegment']
    
    # Parse timestamps
    try:
        start_time = parse_timestamp(obj['startTime'])
        end_time = parse_timestamp(obj['endTime'])
    except (KeyError, ValueError) as e:
        return None
    
    duration_minutes = int((end_time - start_time).total_seconds() / 60)
    
    # Parse locations
    start_location = parse_geo_point(activity.get('start'))
    end_location = parse_geo_point(activity.get('end'))
    
    if not start_location or not end_location:
        return None
    
    # Extract activity type and confidence
    top_candidate = activity.get('topCandidate', {})
    activity_type = normalize_activity_type(top_candidate.get('type', 'UNKNOWN'))
    confidence = float(top_candidate.get('probability', 0.0))
    
    distance_meters = float(activity.get('distanceMeters', 0.0))
    
    # Extract route geometry if available
    route_geometry = extract_route_geometry(activity)
    
    # Build source metadata with new format fields
    source_metadata = {
        'format': 'new_activity_segment',
        'top_candidate': top_candidate,
        'edit_confirmation_status': activity.get('editConfirmationStatus'),
        'parking_event': activity.get('parkingEvent'),
        'has_simplified_raw_path': 'simplifiedRawPath' in activity
    }
    
    return {
        'start_time': start_time,
        'end_time': end_time,
        'duration_minutes': duration_minutes,
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


def parse_breadcrumb_trail(obj):
    """
    Parse standalone timelinePath breadcrumb trail.
    
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
        start_time = parse_timestamp(obj['startTime'])
        end_time = parse_timestamp(obj['endTime'])
    except (KeyError, ValueError) as e:
        return None
    
    duration_minutes = int((end_time - start_time).total_seconds() / 60)
    
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


def import_movements_to_database(conn, movements):
    """
    Import movements into Movements table.
    Links to adjacent visits using temporal and spatial proximity.
    """
    cursor = conn.cursor()
    
    # Check if table is empty
    cursor.execute("SELECT COUNT(*) as count FROM Movements")
    existing_count = cursor.fetchone()['count']
    
    if existing_count > 0:
        print(f"\n⚠ Movements table already has {existing_count:,} records")
        response = input("Delete existing records and re-import? (y/N): ")
        if response.lower() == 'y':
            print("Deleting existing movements...")
            cursor.execute("DELETE FROM Movements")
            conn.commit()
            print("✓ Deleted existing records")
        else:
            print("Aborting import")
            return 0
    
    print("\nImporting movements...")
    
    imported_count = 0
    skipped_count = 0
    linked_to_preceding = 0
    linked_to_following = 0
    
    try:
        for movement in tqdm(movements, desc="Importing movements"):
            start_lat, start_lon = movement['start_location']
            end_lat, end_lon = movement['end_location']
            
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
            
            # Convert source_metadata to JSON string
            source_metadata_json = orjson.dumps(movement['source_metadata']).decode('utf-8') if movement['source_metadata'] else None
            
            # Insert movement
            cursor.execute("""
                INSERT INTO Movements (
                    start_time,
                    end_time,
                    duration_minutes,
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
                    %s, %s, %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                    ST_GeomFromText(%s, 4326)::geography,
                    %s, %s, %s,
                    %s, %s,
                    %s,
                    %s, %s
                )
            """, (
                movement['start_time'],
                movement['end_time'],
                movement['duration_minutes'],
                start_lon, start_lat,  # PostGIS uses lon,lat order
                end_lon, end_lat,
                movement['route_geometry'],
                movement['activity_type'],
                movement['confidence'],
                movement['distance_meters'],
                movement['source'],
                movement['movement_type'],
                source_metadata_json,
                preceding_visit_id,
                following_visit_id
            ))
            
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


def main():
    """Main execution flow"""
    print("="*60)
    print("MOVEMENT IMPORT (Google Timeline)")
    print("="*60)
    
    try:
        config = load_config()
        json_file = Path(config['source_data']['location_history_json'])
        
        if not json_file.exists():
            print(f"\n✗ Location history file not found: {json_file}")
            sys.exit(1)
        
    except Exception as e:
        print(f"\n✗ Error loading configuration: {e}")
        sys.exit(1)
    
    # Connect to database
    conn = get_unified_connection()
    
    try:
        # Parse movements from JSON (convert generator to list for progress bar)
        movements = list(parse_movements_from_json(json_file))
        
        if not movements:
            print("\n✓ No movements found to import")
            return
        
        print(f"✓ Total movements to import: {len(movements):,}")
        
        # Import to database
        imported = import_movements_to_database(conn, movements)
        
        if imported > 0:
            # Print summary
            print_movement_summary(conn)
        
        print("✓ Movement import complete!")
        
    except Exception as e:
        print(f"\n✗ Error during import: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
