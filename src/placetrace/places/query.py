"""
pt-query — Explore your location history from the terminal.

Run pt-query --help for the list of commands.
"""

import argparse

from placetrace.db import get_main_connection


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


def main():
    parser = argparse.ArgumentParser(description="Explore your location history from the terminal.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="show database statistics")

    p = sub.add_parser("nearby", help="find visits near a location")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("radius_km", type=float, nargs="?", default=1.0)

    p = sub.add_parser("top", help="show most visited places")
    p.add_argument("n", type=int, nargs="?", default=10)

    p = sub.add_parser("when", help="when were you at this location?")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("radius_km", type=float, nargs="?", default=0.1)

    p = sub.add_parser("year", help="summarize a specific year")
    p.add_argument("year", type=int)

    p = sub.add_parser("trips", help="list trips, optionally filtered by year")
    p.add_argument("year", type=int, nargs="?")

    p = sub.add_parser("trip", help="details about a specific trip")
    p.add_argument("trip_id", type=int)

    p = sub.add_parser("city", help="show all visits to a city")
    p.add_argument("city_name")

    args = parser.parse_args()

    conn = get_main_connection()

    try:
        if args.command == "stats":
            stats(conn)
        elif args.command == "nearby":
            nearby(conn, args.lat, args.lon, args.radius_km)
        elif args.command == "top":
            top_places(conn, args.n)
        elif args.command == "when":
            when_at(conn, args.lat, args.lon, args.radius_km)
        elif args.command == "year":
            year_summary(conn, args.year)
        elif args.command == "trips":
            list_trips(conn, args.year)
        elif args.command == "trip":
            trip_details(conn, args.trip_id)
        elif args.command == "city":
            city_visits(conn, args.city_name)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
