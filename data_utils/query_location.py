#!/usr/bin/env python3
"""
Location History Query Tool
Simple CLI for exploring your location history database

Usage:
    python query_location.py [command] [args]

Commands:
    stats                          Show database statistics
    nearby LAT LON [RADIUS_KM]     Find visits near a location
    top [N]                        Show top N most visited places
    when LAT LON [RADIUS_KM]       When were you at this location?
    year YEAR                      Summarize a specific year
    trips [YEAR]                   List trips (optionally filtered by year)
    trip TRIP_ID                   Details about a specific trip
    photos LAT LON [RADIUS_KM]     Find photos near a location
    city CITY_NAME                 Show all visits to a city
"""

import sys
from datetime import datetime

# Import database module
from db import get_main_connection


def stats(conn):
    """Show basic statistics about the database"""
    cursor = conn.cursor()
    
    # Visit counts
    cursor.execute("""
        SELECT visit_type, COUNT(*) as count 
        FROM Visits
        GROUP BY visit_type 
        ORDER BY count DESC
    """)
    
    print("\n📊 Database Statistics")
    print("=" * 60)
    print("\nVisits:")
    for row in cursor.fetchall():
        visit_type = row['visit_type'] or 'Untyped'
        print(f"  {visit_type}: {row['count']:,}")
    
    # Photo counts
    cursor.execute("SELECT COUNT(*) as count FROM Photos")
    total_photos = cursor.fetchone()['count']
    
    cursor.execute("""
        SELECT COUNT(*) as count FROM Photos 
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """)
    photos_with_gps = cursor.fetchone()['count']
    
    print(f"\nPhotos:")
    print(f"  Total: {total_photos:,}")
    print(f"  With GPS: {photos_with_gps:,}")
    
    # Trip counts
    cursor.execute("SELECT COUNT(*) as count FROM Trips")
    total_trips = cursor.fetchone()['count']
    
    cursor.execute("""
        SELECT trip_category, COUNT(*) as count
        FROM Trips
        GROUP BY trip_category
        ORDER BY count DESC
    """)
    trips_by_category = cursor.fetchall()
    
    print(f"\nTrips:")
    print(f"  Total: {total_trips:,}")
    for row in trips_by_category:
        print(f"  {row['trip_category']}: {row['count']:,}")
    
    # Location counts
    cursor.execute("SELECT COUNT(*) as count FROM Locations")
    total_locations = cursor.fetchone()['count']
    
    print(f"\nLocations:")
    print(f"  Unique cities/areas: {total_locations:,}")
    
    # Date range
    cursor.execute("""
        SELECT 
            MIN(start_time) as first_visit,
            MAX(end_time) as last_visit
        FROM Visits
    """)
    dates = cursor.fetchone()
    
    print(f"\n📅 Date Range:")
    print(f"  First visit: {dates['first_visit']}")
    print(f"  Last visit:  {dates['last_visit']}")
    
    cursor.close()


def nearby(conn, lat, lon, radius_km=1):
    """Find visits near a specific location"""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            v.start_time,
            v.end_time,
            v.duration_minutes,
            v.semantic_type,
            l.city,
            l.state,
            ST_Distance(
                v.location,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) / 1000 as distance_km
        FROM Visits v
        LEFT JOIN Locations l ON v.location_id = l.id
        WHERE ST_DWithin(
            v.location,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY v.start_time DESC
        LIMIT 50
    """, (lon, lat, lon, lat, radius_km * 1000))
    
    results = cursor.fetchall()
    cursor.close()
    
    print(f"\n🔍 Visits within {radius_km}km of ({lat:.4f}, {lon:.4f})")
    print("=" * 80)
    
    if not results:
        print("  No visits found in this area")
    else:
        for row in results:
            location_str = f"{row['city']}, {row['state']}" if row['city'] else "(unknown)"
            print(f"\n  {row['start_time'].strftime('%Y-%m-%d %H:%M')}")
            print(f"    Location: {location_str}")
            print(f"    Duration: {row['duration_minutes']:.0f} min")
            print(f"    Distance: {row['distance_km']:.2f} km")
            if row['semantic_type']:
                print(f"    Type: {row['semantic_type']}")


def top_places(conn, limit=10):
    """Show most visited places"""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            l.city,
            l.state,
            l.country,
            COUNT(*) as visit_count,
            SUM(v.duration_minutes) / 60.0 as total_hours
        FROM Visits v
        JOIN Locations l ON v.location_id = l.id
        WHERE l.city IS NOT NULL
        GROUP BY l.city, l.state, l.country
        ORDER BY visit_count DESC
        LIMIT %s
    """, (limit,))
    
    results = cursor.fetchall()
    cursor.close()
    
    print(f"\n🏆 Top {limit} Most Visited Places")
    print("=" * 80)
    
    for i, row in enumerate(results, 1):
        location = f"{row['city']}, {row['state']}" if row['state'] else f"{row['city']}, {row['country']}"
        print(f"\n  {i}. {location}")
        print(f"     Visits: {row['visit_count']:,}")
        print(f"     Total time: {row['total_hours']:.0f} hours ({row['total_hours']/24:.1f} days)")


def when_at(conn, lat, lon, radius_km=0.1):
    """When were you at a specific location?"""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            start_time,
            end_time,
            duration_minutes,
            semantic_type,
            visit_type
        FROM Visits
        WHERE ST_DWithin(
            location,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY start_time
    """, (lon, lat, radius_km * 1000))
    
    results = cursor.fetchall()
    cursor.close()
    
    print(f"\n📅 When you were at ({lat:.4f}, {lon:.4f})")
    print("=" * 80)
    
    if not results:
        print(f"  No visits found within {radius_km}km")
    else:
        print(f"  Found {len(results)} visits")
        for row in results:
            print(f"\n  {row['start_time'].strftime('%Y-%m-%d %H:%M')} → "
                  f"{row['end_time'].strftime('%H:%M')}")
            print(f"    Duration: {row['duration_minutes']:.0f} min")
            print(f"    Type: {row['visit_type']}")
            if row['semantic_type']:
                print(f"    Semantic: {row['semantic_type']}")


def year_summary(conn, year):
    """Summarize a specific year"""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            visit_type,
            COUNT(*) as count
        FROM Visits
        WHERE EXTRACT(YEAR FROM start_time) = %s
        GROUP BY visit_type
        ORDER BY count DESC
    """, (year,))
    
    print(f"\n📆 {year} Summary")
    print("=" * 60)
    print("\nVisits:")
    
    for row in cursor.fetchall():
        visit_type = row['visit_type'] or 'Untyped'
        print(f"  {visit_type}: {row['count']:,}")
    
    # Photos taken this year
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM Photos
        WHERE EXTRACT(YEAR FROM capture_datetime) = %s
    """, (year,))
    
    photo_count = cursor.fetchone()['count']
    print(f"\nPhotos taken: {photo_count:,}")
    
    # Trips this year
    cursor.execute("""
        SELECT trip_category, COUNT(*) as count
        FROM Trips
        WHERE EXTRACT(YEAR FROM start_time) = %s
        GROUP BY trip_category
        ORDER BY count DESC
    """, (year,))
    
    trips = cursor.fetchall()
    if trips:
        print(f"\nTrips:")
        for row in trips:
            print(f"  {row['trip_category']}: {row['count']:,}")
    
    cursor.close()


def list_trips(conn, year=None):
    """List trips, optionally filtered by year"""
    cursor = conn.cursor()
    
    if year:
        cursor.execute("""
            SELECT 
                id,
                start_time,
                end_time,
                trip_category,
                cities
            FROM Trips
            WHERE EXTRACT(YEAR FROM start_time) = %s
            ORDER BY start_time
        """, (year,))
        print(f"\n✈️ Trips in {year}")
    else:
        cursor.execute("""
            SELECT 
                id,
                start_time,
                end_time,
                trip_category,
                cities
            FROM Trips
            ORDER BY start_time DESC
            LIMIT 50
        """)
        print(f"\n✈️ Recent Trips (last 50)")
    
    results = cursor.fetchall()
    cursor.close()
    
    print("=" * 80)
    
    if not results:
        print("  No trips found")
    else:
        for row in results:
            duration_days = (row['end_time'] - row['start_time']).days
            cities_str = ', '.join(row['cities'][:3]) if row['cities'] else 'Unknown'
            if row['cities'] and len(row['cities']) > 3:
                cities_str += f" (+{len(row['cities'])-3} more)"
            
            print(f"\n  Trip #{row['id']}: {row['start_time'].strftime('%Y-%m-%d')} → "
                  f"{row['end_time'].strftime('%Y-%m-%d')} ({duration_days} days)")
            print(f"    Category: {row['trip_category']}")
            print(f"    Destinations: {cities_str}")


def trip_details(conn, trip_id):
    """Show details about a specific trip"""
    cursor = conn.cursor()
    
    # Get trip info
    cursor.execute("""
        SELECT 
            id,
            start_time,
            end_time,
            trip_category,
            cities
        FROM Trips
        WHERE id = %s
    """, (trip_id,))
    
    trip = cursor.fetchone()
    
    if not trip:
        print(f"\n✗ Trip #{trip_id} not found")
        return
    
    # Get visit count
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM Trip_Visits
        WHERE trip_id = %s
    """, (trip_id,))
    visit_count = cursor.fetchone()['count']
    
    # Get photo count
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM Trip_Photos
        WHERE trip_id = %s
    """, (trip_id,))
    photo_count = cursor.fetchone()['count']
    
    cursor.close()
    
    duration_days = (trip['end_time'] - trip['start_time']).days
    
    print(f"\n✈️ Trip #{trip['id']} Details")
    print("=" * 80)
    print(f"\nDates: {trip['start_time'].strftime('%Y-%m-%d')} → "
          f"{trip['end_time'].strftime('%Y-%m-%d')}")
    print(f"Duration: {duration_days} days")
    print(f"Category: {trip['trip_category']}")
    print(f"\nDestinations: {', '.join(trip['cities']) if trip['cities'] else 'Unknown'}")
    print(f"\nVisits: {visit_count:,}")
    print(f"Photos: {photo_count:,}")


def photos_near(conn, lat, lon, radius_km=1):
    """Find photos near a location"""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            file_path,
            capture_datetime,
            camera_make,
            camera_model,
            ST_Distance(
                ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) / 1000 as distance_km
        FROM Photos
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND ST_DWithin(
              ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography,
              ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
              %s
          )
        ORDER BY capture_datetime DESC
        LIMIT 50
    """, (lon, lat, lon, lat, radius_km * 1000))
    
    results = cursor.fetchall()
    cursor.close()
    
    print(f"\n📷 Photos within {radius_km}km of ({lat:.4f}, {lon:.4f})")
    print("=" * 80)
    
    if not results:
        print("  No photos found in this area")
    else:
        for row in results:
            path = Path(row['file_path']).name
            dt_str = row['capture_datetime'].strftime('%Y-%m-%d %H:%M') if row['capture_datetime'] else 'Unknown'
            camera = f"{row['camera_make']} {row['camera_model']}" if row['camera_make'] else "Unknown camera"
            
            print(f"\n  {path}")
            print(f"    Date: {dt_str}")
            print(f"    Camera: {camera}")
            print(f"    Distance: {row['distance_km']:.2f} km")


def city_visits(conn, city_name):
    """Show all visits to a specific city"""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            v.start_time,
            v.end_time,
            v.duration_minutes,
            v.semantic_type,
            l.city,
            l.state
        FROM Visits v
        JOIN Locations l ON v.location_id = l.id
        WHERE LOWER(l.city) = LOWER(%s)
        ORDER BY v.start_time DESC
        LIMIT 100
    """, (city_name,))
    
    results = cursor.fetchall()
    cursor.close()
    
    print(f"\n🏙️ Visits to {city_name}")
    print("=" * 80)
    
    if not results:
        print(f"  No visits found to {city_name}")
    else:
        print(f"  Found {len(results)} visits")
        total_hours = sum(r['duration_minutes'] for r in results) / 60.0
        print(f"  Total time: {total_hours:.0f} hours ({total_hours/24:.1f} days)")
        print()
        
        for row in results[:20]:  # Show first 20
            print(f"  {row['start_time'].strftime('%Y-%m-%d %H:%M')}")
            print(f"    Duration: {row['duration_minutes']:.0f} min")
            if row['semantic_type']:
                print(f"    Type: {row['semantic_type']}")


def print_help():
    print(__doc__)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ['-h', '--help', 'help']:
        print_help()
        sys.exit(0)
    
    command = sys.argv[1]
    conn = get_main_connection()
    
    try:
        if command == 'stats':
            stats(conn)
        
        elif command == 'nearby':
            if len(sys.argv) < 4:
                print("Error: nearby requires LAT LON [RADIUS_KM]")
                sys.exit(1)
            lat = float(sys.argv[2])
            lon = float(sys.argv[3])
            radius = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
            nearby(conn, lat, lon, radius)
        
        elif command == 'top':
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
            top_places(conn, limit)
        
        elif command == 'when':
            if len(sys.argv) < 4:
                print("Error: when requires LAT LON [RADIUS_KM]")
                sys.exit(1)
            lat = float(sys.argv[2])
            lon = float(sys.argv[3])
            radius = float(sys.argv[4]) if len(sys.argv) > 4 else 0.1
            when_at(conn, lat, lon, radius)
        
        elif command == 'year':
            if len(sys.argv) < 3:
                print("Error: year requires YEAR")
                sys.exit(1)
            year = int(sys.argv[2])
            year_summary(conn, year)
        
        elif command == 'trips':
            year = int(sys.argv[2]) if len(sys.argv) > 2 else None
            list_trips(conn, year)
        
        elif command == 'trip':
            if len(sys.argv) < 3:
                print("Error: trip requires TRIP_ID")
                sys.exit(1)
            trip_id = int(sys.argv[2])
            trip_details(conn, trip_id)
        
        elif command == 'photos':
            if len(sys.argv) < 4:
                print("Error: photos requires LAT LON [RADIUS_KM]")
                sys.exit(1)
            lat = float(sys.argv[2])
            lon = float(sys.argv[3])
            radius = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
            photos_near(conn, lat, lon, radius)
        
        elif command == 'city':
            if len(sys.argv) < 3:
                print("Error: city requires CITY_NAME")
                sys.exit(1)
            city_name = sys.argv[2]
            city_visits(conn, city_name)
        
        else:
            print(f"Unknown command: {command}")
            print_help()
            sys.exit(1)
    
    finally:
        conn.close()


if __name__ == '__main__':
    from pathlib import Path
    main()
