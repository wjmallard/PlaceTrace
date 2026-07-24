"""
pt-import-visits — Import Google Timeline location history visits.

- Streaming insertions: insert as parsing progresses (Ctrl-C safe)
- Parses location-history.json
- Extracts visit entries only (skips activities/paths)
- Stores lat/lon in geography column
- Extracts local_date and local_time from timezone-aware timestamps
- Sets location_id = NULL (geocoded later by pt-geocode)
- Sets visit_type = 'timeline'
- Resume capability via duplicate detection
"""

import argparse
import traceback

import orjson
from tqdm import tqdm
import sys

from placetrace.db import get_main_connection
from placetrace.config import LOCATION_HISTORY_JSON
from placetrace.pipeline.parse import local_date_time, parse_geo_point, parse_timestamp


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
    Commits in batches so Ctrl-C loses at most the current batch.
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
    
    print("\nImporting visits (streaming)...")
    
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
            local_start_date, local_start_time = local_date_time(start_time_str)
            local_end_date, local_end_time = local_date_time(end_time_str)

            # Calculate duration in minutes (minimum 1 minute)
            duration_minutes = max(1, round((end_time - start_time).total_seconds() / 60))

            point = parse_geo_point(top_candidate.get('placeLocation'))
            if not point:
                skipped += 1
                continue
            lat, lon = point


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
            
            cursor.execute("""
                INSERT INTO Visits (
                    start_time,
                    end_time,
                    duration_minutes,
                    local_start_date,
                    local_start_time,
                    local_end_date,
                    local_end_time,
                    location,
                    location_id,
                    visit_type,
                    semantic_type,
                    place_id
                ) VALUES (
                    %(start_time)s,
                    %(end_time)s,
                    %(duration_minutes)s,
                    %(local_start_date)s,
                    %(local_start_time)s,
                    %(local_end_date)s,
                    %(local_end_time)s,
                    ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
                    NULL,
                    'timeline',
                    %(semantic_type)s,
                    %(place_id)s
                )
            """, {
                'start_time': start_time,
                'end_time': end_time,
                'duration_minutes': duration_minutes,
                'local_start_date': local_start_date,
                'local_start_time': local_start_time,
                'local_end_date': local_end_date,
                'local_end_time': local_end_time,
                'lat': lat,
                'lon': lon,
                'semantic_type': semantic_type,
                'place_id': place_id,
            })
            imported += 1

            # Commit every 100 visits
            if imported % 100 == 0:
                conn.commit()


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
    
    return imported


def print_summary(conn):
    """Print summary statistics"""
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits")
    total = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits WHERE location_id IS NULL")
    ungeocoded = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits WHERE local_start_date IS NOT NULL")
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


def main(argv=None):
    """Main execution flow"""
    parser = argparse.ArgumentParser(description="Import visits from Google Timeline JSON.")
    parser.parse_args(argv)

    print("="*60)
    print("IMPORT VISITS")
    print("="*60)

    # Connect to database
    conn = get_main_connection()
    
    try:
        # Import visits
        imported = import_visits(conn, LOCATION_HISTORY_JSON)
        
        # Print summary
        print_summary(conn)
        
        print("✓ Visit import complete!")
        
    except Exception as e:
        print(f"\n✗ Error during import: {e}", file=sys.stderr)
        traceback.print_exc()
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
