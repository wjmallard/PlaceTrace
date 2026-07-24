#!/usr/bin/env python3
"""
6_geocode.py

Batch geocode all Visits with NULL location_id.
- Finds unique coordinates from Visits
- Geocodes each unique coordinate ONCE
- Batch updates all matching records

Usage:
    python 6_geocode.py
"""

from tqdm import tqdm
import sys
from collections import defaultdict

from placetrace.db import get_main_connection, geocode_point, get_or_create_location
from placetrace.config import config

# For multiprocessing
from multiprocessing import Pool


def worker_geocode(lat_lon):
    """
    Worker function for parallel geocoding.
    Only reads from OSM database (no writes to main database).
    Returns (lat, lon, location_info) tuple.
    """
    lat, lon = lat_lon
    return (lat, lon, geocode_point(lat, lon))


def get_unique_coordinates(conn):
    """
    Get all unique coordinates from Visits needing geocoding.
    Returns:
        - List of (lat, lon) tuples for workers
        - Dict mapping (lat, lon) to record IDs: {(lat, lon): {'visits': [ids]}}
    """
    print("\nFinding unique coordinates to geocode...")

    cursor = conn.cursor()

    # Get unique coordinates from Visits
    cursor.execute("""
        SELECT
            ROUND(ST_Y(location::geometry)::numeric, 6) AS lat,
            ROUND(ST_X(location::geometry)::numeric, 6) AS lon,
            ARRAY_AGG(id) AS visit_ids
        FROM Visits
        WHERE location_id IS NULL
        GROUP BY lat, lon
    """)
    visits_coords = cursor.fetchall()

    cursor.close()

    # Build mapping: (lat, lon) → {visits: [ids]}
    coord_to_records = defaultdict(lambda: {'visits': []})

    for row in visits_coords:
        lat = float(row['lat'])
        lon = float(row['lon'])
        key = (lat, lon)
        coord_to_records[key]['visits'] = row['visit_ids']

    # Create simple list for workers
    coord_list = list(coord_to_records.keys())

    # Calculate totals
    total_unique = len(coord_list)
    total_visits = sum(len(c['visits']) for c in coord_to_records.values())

    print(f"Found {total_unique:,} unique coordinates:")
    print(f"  {total_visits:,} visits need geocoding")
    if total_unique > 0:
        print(f"  Average {total_visits / total_unique:.1f} records per coordinate")

    return coord_list, coord_to_records


def geocode_and_update(conn, coord_list, coord_to_records):
    """
    Geocode coordinates in parallel, update database sequentially.

    Args:
        conn: Database connection (main database)
        coord_list: List of (lat, lon) tuples to geocode
        coord_to_records: Dict mapping (lat, lon) to {'visits': [ids]}

    Returns:
        geocoded_visits count

    Architecture:
        - Worker processes: Read OSM database in parallel (CPU-bound spatial queries)
        - Main thread: Write to main database sequentially (no race conditions)
    """
    print("\nGeocoding coordinates in parallel (sequential database updates)...")

    num_workers = config['processing'].get('num_workers', 4)
    print(f"Using {num_workers} workers for parallel geocoding")

    cursor = conn.cursor()
    geocoded_visits = 0
    failed_coords = 0

    try:
        with Pool(num_workers) as pool:
            # imap_unordered yields results as workers complete (any order)
            # Main thread processes results sequentially
            for lat, lon, location_info in tqdm(
                pool.imap_unordered(worker_geocode, coord_list, chunksize=10),
                total=len(coord_list),
                desc="Geocoding"
            ):
                try:
                    if not location_info:
                        failed_coords += 1
                        continue

                    # Get or create location entry (sequential write)
                    location_id = get_or_create_location(conn, location_info)

                    if not location_id:
                        failed_coords += 1
                        continue

                    # Get record IDs for this coordinate
                    record_ids = coord_to_records[(lat, lon)]

                    # Update all visits at this coordinate
                    if record_ids['visits']:
                        cursor.execute("""
                            UPDATE Visits
                            SET location_id = %s
                            WHERE id = ANY(%s)
                        """, (location_id, record_ids['visits']))
                        geocoded_visits += len(record_ids['visits'])

                    # Commit after each coordinate (ensures progress is saved)
                    conn.commit()

                except Exception as e:
                    # Log error but continue with next coordinate
                    failed_coords += 1
                    conn.rollback()  # Rollback failed coordinate
                    if failed_coords <= 5:  # Only print first few errors
                        print(f"\n  Warning: Failed to process ({lat}, {lon}): {e}")
                    continue

    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user (Ctrl-C)")
        print("Progress has been committed (per-coordinate commits)")
        print(f"✓ Successfully geocoded {geocoded_visits:,} visits")
        print(f"✓ Resume by running script again - it will skip already-geocoded records")
        cursor.close()
        return geocoded_visits

    cursor.close()

    print(f"\n✓ Geocoded {geocoded_visits:,} visits")
    if failed_coords > 0:
        print(f"✗ Failed to geocode {failed_coords:,} unique coordinates")

    return geocoded_visits


def print_summary(conn):
    """Print summary statistics"""
    cursor = conn.cursor()
    
    # Visits stats
    cursor.execute("SELECT COUNT(*) as count FROM Visits")
    total_visits = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits WHERE location_id IS NOT NULL")
    visits_geocoded = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Visits WHERE location_id IS NULL")
    visits_ungeocoded = cursor.fetchone()['count']
    
    # Locations stats
    cursor.execute("SELECT COUNT(*) as count FROM Locations")
    total_locations = cursor.fetchone()['count']
    
    cursor.execute("""
        SELECT admin_level, COUNT(*) as count
        FROM Locations 
        GROUP BY admin_level 
        ORDER BY admin_level DESC
    """)
    locations_by_level = cursor.fetchall()
    
    cursor.close()
    
    print("\n" + "="*60)
    print("GEOCODING SUMMARY")
    print("="*60)
    
    print(f"\nVisits:")
    print(f"  Total:               {total_visits:>8,}")
    print(f"  Geocoded:            {visits_geocoded:>8,} ({visits_geocoded/total_visits*100 if total_visits > 0 else 0:.1f}%)")
    print(f"  Not geocoded:        {visits_ungeocoded:>8,}")

    print(f"\nLocations:")
    print(f"  Total unique:        {total_locations:>8,}")
    print(f"  By detail level:")
    for row in locations_by_level:
        level_name = {8: 'City', 6: 'County', 4: 'State', 2: 'Country'}.get(row['admin_level'], f'Level {row["admin_level"]}')
        print(f"    {level_name:<20} {row['count']:>8,}")
    
    print("="*60 + "\n")


def main():
    """Main execution flow"""
    print("="*60)
    print("BATCH GEOCODING")
    print("="*60)
    
    # Connect to database
    conn = get_main_connection()
    
    try:
        # Step 1: Get unique coordinates needing geocoding
        coord_list, coord_to_records = get_unique_coordinates(conn)
        
        if not coord_list:
            print("\n✓ Nothing to geocode - all records already have location_id")
            return
        
        # Step 2: Geocode in parallel and update sequentially
        geocode_and_update(conn, coord_list, coord_to_records)
        
        # Step 3: Print summary
        print_summary(conn)
        
        print("✓ Batch geocoding complete!")
        
    except Exception as e:
        print(f"\n✗ Error during geocoding: {e}", file=sys.stderr)
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
