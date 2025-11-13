#!/usr/bin/env python3
"""
2_import_visits.py

Import Google Timeline location history visits.
- Streaming insertions: insert as parsing progresses (Ctrl-C safe)
- Parses location-history.json
- Extracts visit entries only (skips activities/paths)
- Stores lat/lon in geography column
- Extracts local_date and local_time from timezone-aware timestamps
- Sets location_id = NULL (will be geocoded by 4_geocode.py)
- Sets visit_type = 'timeline'
- Resume capability via duplicate detection

Usage:
    python 2_import_visits.py
"""

import orjson
from datetime import datetime, timezone, timedelta
from tqdm import tqdm
import sys
import re

# Import database module
from db import get_main_connection, config


def parse_geo_string(geo_str):
    """Parse 'geo:lat,lon' format into (lat, lon) tuple."""
    coords = geo_str.replace('geo:', '').split(',')
    return float(coords[0]), float(coords[1])


def extract_timezone_offset(timestamp_str):
    """
    Extract timezone offset from ISO 8601 timestamp string.
    Returns a timezone object representing the offset.
    
    Examples:
        "2024-05-18T07:54:00.030+02:00" -> UTC+02:00
        "2024-05-18T05:54:00.030Z" -> UTC
    """
    # Handle 'Z' suffix (UTC)
    if timestamp_str.endswith('Z'):
        return timezone.utc
    
    # Extract offset pattern: +HH:MM or -HH:MM
    match = re.search(r'([+-])(\d{2}):(\d{2})$', timestamp_str)
    if match:
        sign = 1 if match.group(1) == '+' else -1
        hours = int(match.group(2))
        minutes = int(match.group(3))
        offset_seconds = sign * (hours * 3600 + minutes * 60)
        return timezone(timedelta(seconds=offset_seconds))
    
    # Fallback to UTC if we can't parse the offset
    return timezone.utc


def parse_timestamp(ts_str):
    """Parse Google Timeline timestamp to datetime, convert to UTC"""
    dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    # Convert to UTC explicitly before passing to database
    return dt.astimezone(timezone.utc)


def extract_local_date_time(timestamp_str):
    """
    Extract local date and time from timezone-aware timestamp string.
    Returns (date, time) tuple representing wall-clock values.
    
    Example:
        "2024-05-18T07:54:00.030+02:00" -> (date(2024, 5, 18), time(7, 54, 0, 30000))
    """
    # Parse with timezone info preserved
    dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    
    # Extract the offset from original string
    tz_offset = extract_timezone_offset(timestamp_str)
    
    # Convert to local time using the offset
    local_dt = dt.astimezone(tz_offset)
    
    return local_dt.date(), local_dt.time()


def get_existing_visits(conn):
    """
    Get set of (start_time, end_time, lat, lon) tuples already in database.
    Used for resume capability - skip visits already imported.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            start_time,
            end_time,
            ROUND(ST_Y(location::geometry)::numeric, 6) AS lat,
            ROUND(ST_X(location::geometry)::numeric, 6) AS lon
        FROM Visits
        WHERE visit_type = 'timeline'
    """)
    existing = {(row['start_time'], row['end_time'], float(row['lat']), float(row['lon'])) 
                for row in cursor.fetchall()}
    cursor.close()
    return existing


def import_visits(conn, json_path):
    """
    Import visits from Google Timeline JSON with streaming insertion.
    Uses savepoints for per-insert isolation and Ctrl-C safety.
    """
    print(f"Loading location history from: {json_path}")
    
    with open(json_path, 'rb') as f:
        data = orjson.loads(f.read())
    
    # Handle both formats: list or dict with 'timelineObjects' key
    if isinstance(data, list):
        timeline_objects = data
    else:
        timeline_objects = data.get('timelineObjects', [])
    
    print(f"Found {len(timeline_objects):,} timeline objects")
    
    # Filter to just visits (key is 'visit' not 'placeVisit')
    visits = [obj for obj in timeline_objects if 'visit' in obj]
    print(f"Found {len(visits):,} visit entries")
    
    # Get existing visits for resume capability
    print("Checking for existing visits in database...")
    existing_visits = get_existing_visits(conn)
    print(f"Found {len(existing_visits):,} visits already in database")
    
    cursor = conn.cursor()
    imported = 0
    skipped = 0
    
    print("\nImporting visits (streaming with savepoints)...")
    
    try:
        for obj in tqdm(visits, desc="Importing"):
            visit = obj['visit']
            top_candidate = visit['topCandidate']
            
            # Extract timestamps from top level (as strings for local time extraction)
            start_time_str = obj['startTime']
            end_time_str = obj['endTime']
            
            # Parse to UTC for database storage
            start_time = parse_timestamp(start_time_str)
            end_time = parse_timestamp(end_time_str)
            
            # Extract local date and time from original timezone-aware strings
            local_date, local_time = extract_local_date_time(start_time_str)
            
            # Calculate duration in minutes
            duration_minutes = int((end_time - start_time).total_seconds() / 60)
            
            # Parse location using original parse_geo_string function
            try:
                lat, lon = parse_geo_string(top_candidate['placeLocation'])
            except (KeyError, ValueError):
                skipped += 1
                continue
            
            # Round coordinates to 6 decimals for comparison (same as database)
            lat_rounded = round(lat, 6)
            lon_rounded = round(lon, 6)
            
            # Check if this visit already exists (resume capability)
            if (start_time, end_time, lat_rounded, lon_rounded) in existing_visits:
                skipped += 1
                continue
            
            # Extract optional fields
            place_id = top_candidate.get('placeID')
            semantic_type = top_candidate.get('semanticType')
            
            # Insert visit with savepoint for isolation
            try:
                cursor.execute("SAVEPOINT insert_visit")
                
                cursor.execute("""
                    INSERT INTO Visits (
                        start_time,
                        end_time,
                        duration_minutes,
                        local_date,
                        local_time,
                        location,
                        location_id,
                        visit_type,
                        semantic_type,
                        place_id
                    ) VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        NULL,
                        'timeline',
                        %s,
                        %s
                    )
                """, (
                    start_time,
                    end_time,
                    duration_minutes,
                    local_date,
                    local_time,
                    lon, lat,  # PostGIS uses lon, lat order
                    semantic_type,
                    place_id
                ))
                
                cursor.execute("RELEASE SAVEPOINT insert_visit")
                imported += 1
                
                # Commit every 100 visits
                if imported % 100 == 0:
                    conn.commit()
                    
            except Exception as e:
                # Handle duplicate (unlikely but possible)
                if 'duplicate key' in str(e) or 'UniqueViolation' in str(type(e)):
                    cursor.execute("ROLLBACK TO SAVEPOINT insert_visit")
                    cursor.execute("RELEASE SAVEPOINT insert_visit")
                    skipped += 1
                    continue
                else:
                    # Re-raise unexpected errors
                    raise
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user (Ctrl-C)")
        print("Committing current batch...")
        conn.commit()
        print(f"✓ Safely committed {imported:,} visits before interruption")
        print(f"✓ Resume by running script again - it will skip already-imported visits")
        cursor.close()
        return imported
    
    # Final commit
    conn.commit()
    cursor.close()
    
    print(f"\n✓ Imported {imported:,} new visits")
    print(f"⊘ Skipped {skipped:,} visits (missing coords or already in database)")
    print(f"\nNote: location_id is NULL - run 4_geocode.py to populate")
    
    return imported


def print_summary(conn):
    """Print summary statistics"""
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits")
    total = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits WHERE location_id IS NULL")
    ungeocoded = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits WHERE local_date IS NOT NULL")
    with_local_time = cursor.fetchone()['count']
    
    cursor.execute("""
        SELECT visit_type, COUNT(*) as count
        FROM Visits 
        GROUP BY visit_type 
        ORDER BY COUNT(*) DESC
    """)
    by_type = cursor.fetchall()
    
    cursor.close()
    
    print("\n" + "="*60)
    print("VISITS IMPORT SUMMARY")
    print("="*60)
    print(f"\nTotal visits:        {total:>8,}")
    print(f"Needs geocoding:     {ungeocoded:>8,}")
    print(f"With local date:     {with_local_time:>8,}")
    print(f"\nVisits by type:")
    for row in by_type:
        print(f"  {row['visit_type'] or 'NULL':<20} {row['count']:>8,}")
    print("="*60 + "\n")


def main():
    """Main execution flow"""
    print("="*60)
    print("IMPORT VISITS")
    print("="*60)
    
    # Get JSON path from config
    json_path = config['source_data']['location_history_json']
    
    # Connect to database
    conn = get_main_connection()
    
    try:
        # Import visits
        imported = import_visits(conn, json_path)
        
        # Print summary
        print_summary(conn)
        
        print("✓ Visit import complete!")
        
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
