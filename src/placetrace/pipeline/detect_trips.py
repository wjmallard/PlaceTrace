#!/usr/bin/env python3
"""
7_detect_trips.py

Detect and categorize trips from location history:
- Loads home/work locations from JSON files (date-aware)
- Uses PostGIS for all distance calculations
- Checks for connecting Movements when evaluating visit gaps
- Categorizes trips: Day Trip, Short Trip, Long Trip
- Populates Trips and Trip_Visits tables

Requirements:
- data/home_locations.json - Date-aware home locations
- data/work_locations.json - Date-aware work locations
- trips section in config.yaml - Trip category definitions

Usage:
    python 7_detect_trips.py
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm
import sys

from placetrace.db import get_main_connection
from placetrace.config import config, project_root


def load_locations_json(filename):
    """
    Load home or work locations from JSON file.
    Returns list of location dicts with parsed dates.
    """
    filepath = project_root / "data" / filename
    
    if not filepath.exists():
        raise FileNotFoundError(
            f"Required file not found: {filepath}\n"
            f"Please create data/{filename} with location data."
        )
    
    with open(filepath, 'r') as f:
        locations = json.load(f)
    
    # Parse date strings to date objects
    for loc in locations:
        loc['start_date'] = datetime.strptime(loc['start_date'], '%Y-%m-%d').date()
        loc['end_date'] = datetime.strptime(loc['end_date'], '%Y-%m-%d').date()
    
    return locations


def load_trip_config():
    """Load trip configuration from config.yaml trips section."""
    return config['trips']


def get_home_at_date(home_locations, date):
    """
    Get home location active on a specific date.
    Returns home dict or None if no home defined for that date.
    """
    for home in home_locations:
        if home['start_date'] <= date <= home['end_date']:
            return home
    return None


def get_work_at_date(work_locations, date):
    """
    Get work location active on a specific date.
    Returns work dict or None if no work defined for that date.
    """
    for work in work_locations:
        if work['start_date'] <= date <= work['end_date']:
            return work
    return None


def fetch_all_visits(conn):
    """
    Fetch all visits with their locations, ordered by time.
    Returns list of visit dicts.
    """
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            id,
            start_time,
            end_time,
            duration_minutes,
            ST_Y(location::geometry) as lat,
            ST_X(location::geometry) as lon,
            location_id,
            visit_type,
            place_id
        FROM Visits
        ORDER BY start_time
    """)
    
    visits = cursor.fetchall()
    cursor.close()
    
    return visits


def absorb_orphan_visits(conn, trips, home_locations):
    """
    Find single-visit 'trips' and merge them into adjacent trips.
    
    Orphan visits are typically GPS glitches:
    - Single visit in a trip
    - Very short duration (<30 minutes)
    
    Logic:
    1. Identify orphan visits
    2. Find closest trip before/after in time
    3. If gap < 24 hours, merge into that trip
    4. Otherwise, discard as isolated glitch
    
    Returns cleaned list of trips with orphans absorbed.
    """
    if not trips:
        return trips
    
    # Identify orphans: single visit with <30min duration
    orphans = []
    non_orphans = []
    
    for trip in trips:
        if len(trip['visit_ids']) == 1 and trip['duration_hours'] < 0.5:
            orphans.append(trip)
        else:
            non_orphans.append(trip)
    
    if not orphans:
        return trips
    
    print(f"\n🔍 Found {len(orphans)} orphan visits (likely GPS glitches)")
    
    # Sort non-orphans by time for efficient searching
    non_orphans.sort(key=lambda t: t['start_time'])
    
    merged_count = 0
    discarded_count = 0
    
    for orphan in orphans:
        orphan_time = orphan['start_time']
        
        # Find trips before and after this orphan
        trips_before = [t for t in non_orphans if t['end_time'] < orphan_time]
        trips_after = [t for t in non_orphans if t['start_time'] > orphan_time]
        
        closest_before = max(trips_before, key=lambda t: t['end_time']) if trips_before else None
        closest_after = min(trips_after, key=lambda t: t['start_time']) if trips_after else None
        
        # Calculate time gaps
        gap_before_hours = (orphan_time - closest_before['end_time']).total_seconds() / 3600 if closest_before else float('inf')
        gap_after_hours = (closest_after['start_time'] - orphan_time).total_seconds() / 3600 if closest_after else float('inf')
        
        # Merge into closest trip if gap < 24 hours
        MAX_GAP_HOURS = 24
        
        if gap_before_hours < MAX_GAP_HOURS and gap_before_hours <= gap_after_hours:
            # Merge into trip before
            closest_before['visit_ids'].extend(orphan['visit_ids'])
            closest_before['location_ids'].extend(orphan['location_ids'])
            closest_before['end_time'] = max(closest_before['end_time'], orphan['end_time'])
            closest_before['duration_hours'] = (closest_before['end_time'] - closest_before['start_time']).total_seconds() / 3600
            print(f"  ✓ Merged orphan at {orphan_time.date()} into trip ending {closest_before['end_time'].date()} (gap: {gap_before_hours:.1f}h)")
            merged_count += 1
            
        elif gap_after_hours < MAX_GAP_HOURS:
            # Merge into trip after
            closest_after['visit_ids'] = orphan['visit_ids'] + closest_after['visit_ids']
            closest_after['location_ids'] = orphan['location_ids'] + closest_after['location_ids']
            closest_after['start_time'] = min(closest_after['start_time'], orphan['start_time'])
            closest_after['duration_hours'] = (closest_after['end_time'] - closest_after['start_time']).total_seconds() / 3600
            print(f"  ✓ Merged orphan at {orphan_time.date()} into trip starting {closest_after['start_time'].date()} (gap: {gap_after_hours:.1f}h)")
            merged_count += 1
            
        else:
            # Too isolated - discard
            print(f"  ⚠ Orphan at {orphan_time.date()} too isolated (gaps: {gap_before_hours:.1f}h before, {gap_after_hours:.1f}h after) - discarding")
            discarded_count += 1
    
    print(f"📊 Orphan summary: {merged_count} merged, {discarded_count} discarded")
    
    return non_orphans


def is_home_visit(conn, visit, home_locations):
    """
    Check if visit is at home using PostGIS distance calculation.
    Returns True if within 20km of home active on that date.
    """
    visit_date = visit['start_time'].date()
    home = get_home_at_date(home_locations, visit_date)
    
    if not home:
        return False  # No home defined for this date
    
    cursor = conn.cursor()
    
    # Calculate distance using PostGIS
    cursor.execute("""
        SELECT ST_Distance(
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
        ) / 1000 as distance_km
    """, (visit['lon'], visit['lat'], home['lon'], home['lat']))
    
    result = cursor.fetchone()
    cursor.close()
    
    distance_km = result['distance_km']
    return distance_km <= 20  # 20km radius = home area


def is_work_visit(conn, visit, work_locations):
    """
    Check if visit is at work using PostGIS distance calculation.
    Returns True if within 1km of work active on that date.
    """
    visit_date = visit['start_time'].date()
    work = get_work_at_date(work_locations, visit_date)
    
    if not work:
        return False  # No work defined for this date
    
    # Check place_id first (exact match)
    if visit['place_id'] and visit['place_id'] == work['place_id']:
        return True
    
    cursor = conn.cursor()
    
    # Calculate distance using PostGIS
    cursor.execute("""
        SELECT ST_Distance(
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
        ) / 1000 as distance_km
    """, (visit['lon'], visit['lat'], work['lon'], work['lat']))
    
    result = cursor.fetchone()
    cursor.close()
    
    distance_km = result['distance_km']
    return distance_km <= 1  # 1km radius = work area


def get_activity_between_visits(conn, prev_visit_end, current_visit_start):
    """
    Check if there's a Movement connecting two visits.
    
    Returns:
        - Movement dict if connecting movement exists
        - None if no movement found (data gap)
    """
    cursor = conn.cursor()
    
    # Look for movement that starts near prev_visit end time
    # and ends near current_visit start time
    cursor.execute("""
        SELECT 
            id,
            activity_type,
            distance_meters,
            duration_minutes
        FROM Movements
        WHERE start_time BETWEEN %s AND %s
          AND end_time BETWEEN %s AND %s
        ORDER BY start_time
        LIMIT 1
    """, (
        prev_visit_end - timedelta(minutes=5),
        prev_visit_end + timedelta(minutes=5),
        current_visit_start - timedelta(minutes=5),
        current_visit_start + timedelta(minutes=5)
    ))
    
    result = cursor.fetchone()
    cursor.close()
    
    return result


def should_split_trip(conn, prev_visit, current_visit, home_locations, trip_config):
    """
    Determine if gap between visits should split the trip.
    
    Logic:
    1. If connecting activity exists -> continue trip
    2. If gap < max_gap_hours (8h) -> continue trip
    3. If gap < extended_gap_hours (24h) AND both visits far from home -> continue trip
    4. Otherwise -> split trip
    
    Returns True if trip should be split, False to continue.
    """
    gap_duration = current_visit['start_time'] - prev_visit['end_time']
    gap_hours = gap_duration.total_seconds() / 3600
    
    # Check for connecting activity
    connecting_activity = get_activity_between_visits(
        conn,
        prev_visit['end_time'],
        current_visit['start_time']
    )
    
    if connecting_activity:
        # Movement data exists - continue trip regardless of duration
        return False
    
    # No activity data - use time and distance heuristics
    max_gap_hours = trip_config['detection']['max_gap_hours']
    extended_gap_hours = trip_config['detection'].get('extended_gap_hours', max_gap_hours)
    extended_gap_min_distance = trip_config['detection'].get('extended_gap_min_distance_km', 80)
    
    if gap_hours < max_gap_hours:
        # Short gap - continue trip
        return False
    
    if gap_hours < extended_gap_hours:
        # Medium gap (8-24h) - check if both visits are far from home
        # If both are far from home, this is likely the same trip (sleeping, long travel, etc.)
        
        # Get home location for the time period
        trip_date = prev_visit['end_time'].date()
        home = get_home_at_date(home_locations, trip_date)
        
        if home:
            cursor = conn.cursor()
            
            # Calculate distance from home for both visits
            cursor.execute("""
                SELECT 
                    ST_Distance(
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        prev_loc.location
                    ) / 1000 as prev_dist,
                    ST_Distance(
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        curr_loc.location
                    ) / 1000 as curr_dist
                FROM 
                    Visits prev_loc,
                    Visits curr_loc
                WHERE 
                    prev_loc.id = %s AND
                    curr_loc.id = %s
            """, (
                home['lon'], home['lat'],
                home['lon'], home['lat'],
                prev_visit['id'],
                current_visit['id']
            ))
            
            result = cursor.fetchone()
            cursor.close()
            
            prev_dist = result['prev_dist']
            curr_dist = result['curr_dist']
            
            # If both visits are far from home, continue the trip despite the gap
            if prev_dist >= extended_gap_min_distance and curr_dist >= extended_gap_min_distance:
                return False
    
    # Long gap or gap near home - split trip
    return True


def detect_trips(conn, visits, home_locations, work_locations, trip_config):
    """
    Detect trips from visit sequence.
    
    Algorithm:
    1. Iterate through visits chronologically
    2. Mark each as home/work/away
    3. Check for connecting activities when evaluating gaps
    4. Group consecutive away visits into trips
    5. Filter by minimum distance from home
    6. Categorize by duration
    
    Returns list of trip dicts.
    """
    min_distance_km = trip_config['detection']['min_distance_km']
    max_gap_hours = trip_config['detection']['max_gap_hours']
    
    print(f"\nDetecting trips (min distance: {min_distance_km}km, max gap: {max_gap_hours}h)...")
    print("  (Using Movements to bridge travel gaps)")
    
    trips = []
    current_trip_visits = []
    last_visit_end = None
    
    for visit in tqdm(visits, desc="Analyzing visits"):
        # Check if at home or work
        at_home = is_home_visit(conn, visit, home_locations)
        at_work = is_work_visit(conn, visit, work_locations)
        
        # Check if gap should split trip (uses activity data)
        if last_visit_end and current_trip_visits:
            should_split = should_split_trip(
                conn,
                current_trip_visits[-1],  # Previous visit
                visit,  # Current visit
                home_locations,
                trip_config
            )
        else:
            should_split = False
        
        if at_home or at_work:
            # Back home/at work - finalize current trip if exists
            if current_trip_visits:
                trip = finalize_trip(conn, current_trip_visits, home_locations, trip_config)
                if trip:  # Only add if meets distance criteria
                    trips.append(trip)
                current_trip_visits = []
        elif should_split:
            # Gap should split trip - finalize current and start new one
            if current_trip_visits:
                trip = finalize_trip(conn, current_trip_visits, home_locations, trip_config)
                if trip:
                    trips.append(trip)
            current_trip_visits = [visit]
        else:
            # Away from home - add to current trip
            current_trip_visits.append(visit)
        
        last_visit_end = visit['end_time']
    
    # Finalize last trip if exists
    if current_trip_visits:
        trip = finalize_trip(conn, current_trip_visits, home_locations, trip_config)
        if trip:
            trips.append(trip)
    
    print(f"✓ Detected {len(trips):,} trips")
    
    # Absorb orphan visits (single-visit glitches) into adjacent trips
    trips = absorb_orphan_visits(conn, trips, home_locations)
    
    print(f"✓ Final trip count: {len(trips):,} trips")
    
    return trips


def finalize_trip(conn, trip_visits, home_locations, trip_config):
    """
    Finalize a trip by calculating stats and checking criteria.
    Returns trip dict or None if doesn't meet criteria.
    """
    if not trip_visits:
        return None
    
    start_time = trip_visits[0]['start_time']
    end_time = trip_visits[-1]['end_time']
    duration_hours = (end_time - start_time).total_seconds() / 3600
    
    # Calculate centroid of trip using PostGIS
    visit_ids = [v['id'] for v in trip_visits]
    location_ids = [v['location_id'] for v in trip_visits if v['location_id']]
    
    cursor = conn.cursor()
    
    # Get trip centroid
    cursor.execute("""
        SELECT 
            ST_Y(ST_Centroid(ST_Collect(location::geometry))) as centroid_lat,
            ST_X(ST_Centroid(ST_Collect(location::geometry))) as centroid_lon
        FROM Visits
        WHERE id = ANY(%s)
    """, (visit_ids,))
    
    centroid = cursor.fetchone()
    
    # Calculate distance from home
    trip_date = start_time.date()
    home = get_home_at_date(home_locations, trip_date)
    
    if home:
        cursor.execute("""
            SELECT ST_Distance(
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) / 1000 as distance_km
        """, (centroid['centroid_lon'], centroid['centroid_lat'], 
              home['lon'], home['lat']))
        
        result = cursor.fetchone()
        distance_from_home = result['distance_km']
    else:
        # No home defined - can't filter by distance
        distance_from_home = float('inf')
    
    cursor.close()
    
    # Check minimum distance criteria
    min_distance_km = trip_config['detection']['min_distance_km']
    if distance_from_home < min_distance_km:
        return None  # Too close to home
    
    # Categorize trip by duration
    trip_category = categorize_trip(duration_hours, trip_config)
    
    # If duration doesn't match any category, it's not a trip
    if trip_category is None:
        return None  # Too short to be a trip
    
    # Extract local date/time from start and end timestamps
    local_start_date = start_time.date()
    local_start_time = start_time.time()
    local_end_date = end_time.date()
    local_end_time = end_time.time()
    
    return {
        'start_time': start_time,
        'end_time': end_time,
        'duration_hours': duration_hours,
        'trip_category': trip_category,
        'visit_ids': visit_ids,
        'location_ids': location_ids,
        'distance_from_home_km': distance_from_home,
        'local_start_date': local_start_date,
        'local_start_time': local_start_time,
        'local_end_date': local_end_date,
        'local_end_time': local_end_time
    }


def categorize_trip(duration_hours, trip_config):
    """
    Categorize trip by duration using config thresholds.
    Returns category name or None if duration doesn't match any category.
    """
    categories = trip_config['categories']
    
    for category in categories:
        min_hours = category.get('min_hours', 0)
        max_hours = category.get('max_hours')
        
        # Handle open-ended categories (max_hours is None/null)
        if max_hours is None:
            if duration_hours >= min_hours:
                return category['name']
        else:
            if min_hours <= duration_hours < max_hours:
                return category['name']
    
    # No category matched - not a valid trip
    return None


def get_locations_for_trip(conn, location_ids):
    """
    Get location details for trip display name generation.
    Returns list of location dicts.
    """
    if not location_ids:
        return []
    
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            id,
            city,
            county,
            state,
            country,
            admin_level
        FROM Locations
        WHERE id = ANY(%s)
    """, (location_ids,))
    
    locations = cursor.fetchall()
    cursor.close()
    
    return locations


def generate_trip_display_name(locations):
    """
    Generate human-readable display name from trip locations.
    Logic: Use most specific common administrative level.
    
    Examples:
    - Single city: "Boston"
    - Multiple cities, same state: "Massachusetts"
    - Multiple states, same country: "United States"
    - Multiple countries: "Europe" or country list
    """
    if not locations:
        return "Unknown Location"
    
    # Get unique values at each admin level
    cities = set(loc['city'] for loc in locations if loc['city'])
    counties = set(loc['county'] for loc in locations if loc['county'])
    states = set(loc['state'] for loc in locations if loc['state'])
    countries = set(loc['country'] for loc in locations)
    
    # Use most specific common level
    if len(cities) == 1:
        return list(cities)[0]
    elif len(counties) == 1 and len(cities) <= 3:
        # Small number of cities in same county
        return list(counties)[0]
    elif len(states) == 1:
        return list(states)[0]
    elif len(countries) == 1:
        return list(countries)[0]
    else:
        # Multiple countries - list them
        country_list = sorted(countries)
        if len(country_list) <= 3:
            return ", ".join(country_list)
        else:
            return f"{len(country_list)} Countries"


def get_cities_for_trip(conn, location_ids):
    """
    Get city names for a trip from location_ids.
    Returns list of city names.
    """
    if not location_ids:
        return []
    
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT city
        FROM Locations
        WHERE id = ANY(%s)
          AND city IS NOT NULL
        ORDER BY city
    """, (location_ids,))
    
    cities = [row['city'] for row in cursor.fetchall()]
    cursor.close()
    
    return cities


def insert_trips_to_database(conn, trips):
    """
    Insert detected trips into Trips and Trip_Visits tables.
    Returns number of trips inserted.
    """
    if not trips:
        print("\nNo trips to insert")
        return 0
    
    print(f"\nInserting {len(trips):,} trips into database...")
    
    cursor = conn.cursor()
    inserted = 0
    
    try:
        for trip in tqdm(trips, desc="Inserting trips"):
            # Get location data for display name generation
            locations = get_locations_for_trip(conn, trip['location_ids'])
            display_name = generate_trip_display_name(locations)
            
            # Get cities for the cities array (keep existing functionality)
            cities = get_cities_for_trip(conn, trip['location_ids'])
            
            # Determine primary location_id (most common)
            if trip['location_ids']:
                # Count location_id frequency
                from collections import Counter
                location_counter = Counter(trip['location_ids'])
                primary_location_id = location_counter.most_common(1)[0][0]
            else:
                primary_location_id = None
            
            # Insert trip with display_name
            cursor.execute("""
                INSERT INTO Trips (
                    start_time,
                    end_time,
                    local_start_date,
                    local_start_time,
                    local_end_date,
                    local_end_time,
                    trip_category,
                    cities,
                    primary_location_id,
                    display_name
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (start_time, end_time) DO NOTHING
                RETURNING id
            """, (
                trip['start_time'],
                trip['end_time'],
                trip['local_start_date'],
                trip['local_start_time'],
                trip['local_end_date'],
                trip['local_end_time'],
                trip['trip_category'],
                cities,
                primary_location_id,
                display_name
            ))
            
            result = cursor.fetchone()
            if not result:
                continue  # Duplicate trip, skip
            
            trip_id = result['id']
            inserted += 1
            
            # Insert Trip_Visits junction records
            for visit_id in trip['visit_ids']:
                cursor.execute("""
                    INSERT INTO Trip_Visits (trip_id, visit_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (trip_id, visit_id))
            
            # Commit every 100 trips
            if inserted % 100 == 0:
                conn.commit()
        
        # Final commit
        conn.commit()
        
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user (Ctrl-C)")
        print("Committing current progress...")
        conn.commit()
        print(f"✓ Inserted {inserted:,} trips before interruption")
        cursor.close()
        return inserted
    
    cursor.close()
    
    print(f"✓ Inserted {inserted:,} trips")
    
    return inserted


def print_trip_summary(conn, trip_config):
    """Print summary statistics about detected trips"""
    cursor = conn.cursor()
    
    # Total trips
    cursor.execute("SELECT COUNT(*) as count FROM Trips")
    total_trips = cursor.fetchone()['count']
    
    # Trips by category
    cursor.execute("""
        SELECT trip_category, COUNT(*) as count
        FROM Trips
        GROUP BY trip_category
        ORDER BY 
            CASE trip_category
                WHEN 'Day Trip' THEN 1
                WHEN 'Short Trip' THEN 2
                WHEN 'Long Trip' THEN 3
                ELSE 4
            END
    """)
    trips_by_category = cursor.fetchall()
    
    # Total visits in trips
    cursor.execute("SELECT COUNT(*) as count FROM Trip_Visits")
    total_trip_visits = cursor.fetchone()['count']
    
    # Top destinations
    cursor.execute("""
        SELECT 
            UNNEST(cities) as city,
            COUNT(*) as trip_count
        FROM Trips
        WHERE cities IS NOT NULL AND array_length(cities, 1) > 0
        GROUP BY city
        ORDER BY trip_count DESC
        LIMIT 10
    """)
    top_destinations = cursor.fetchall()
    
    cursor.close()
    
    print("\n" + "="*60)
    print("TRIP DETECTION SUMMARY")
    print("="*60)
    
    print(f"\nTotal trips:         {total_trips:>8,}")
    
    if trips_by_category:
        print(f"\nTrips by category:")
        for row in trips_by_category:
            # Get emoji from config
            category_info = next(
                (c for c in trip_config['categories'] if c['name'] == row['trip_category']),
                None
            )
            emoji = category_info['emoji'] if category_info else '📍'
            print(f"  {emoji} {row['trip_category']:<15} {row['count']:>8,}")
    
    print(f"\nVisits in trips:     {total_trip_visits:>8,}")
    
    if top_destinations:
        print(f"\nTop destinations:")
        for row in top_destinations:
            print(f"  {row['city']:<30} {row['trip_count']:>3,} trips")
    
    print("="*60 + "\n")


def main():
    """Main execution flow"""
    print("="*60)
    print("TRIP DETECTION (Activity-Aware)")
    print("="*60)
    
    try:
        # Load required configuration files
        print("\nLoading configuration files...")
        home_locations = load_locations_json('home_locations.json')
        work_locations = load_locations_json('work_locations.json')
        trip_config = load_trip_config()
        
        print(f"✓ Loaded {len(home_locations)} home locations")
        print(f"✓ Loaded {len(work_locations)} work locations")
        print(f"✓ Loaded {len(trip_config['categories'])} trip categories")
        
    except FileNotFoundError as e:
        print(f"\n✗ {e}")
        sys.exit(1)
    
    # Connect to database
    conn = get_main_connection()
    force = '--force' in sys.argv

    try:
        # Wipe existing trips if --force
        if force:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trip_visits")
            cursor.execute("DELETE FROM trips")
            conn.commit()
            cursor.close()
            print("⚠ Deleted existing trips (--force)")

        # Check if Movements table has data
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM Movements")
        movement_count = cursor.fetchone()['count']
        cursor.close()
        
        if movement_count == 0:
            print("\n⚠ Warning: Movements table is empty")
            print("   Consider importing movements first")
            print("   Trip detection will proceed without movement data\n")
        else:
            print(f"✓ Found {movement_count:,} movements for gap analysis")
        
        # Step 1: Fetch all visits
        print("\nFetching visits from database...")
        visits = fetch_all_visits(conn)
        print(f"✓ Loaded {len(visits):,} visits")
        
        if not visits:
            print("\n✗ No visits found in database")
            print("   Import visits and movements first")
            return
        
        # Step 2: Detect trips
        trips = detect_trips(conn, visits, home_locations, work_locations, trip_config)
        
        if not trips:
            print("\n✓ No trips detected")
            return
        
        # Step 3: Insert trips into database
        inserted = insert_trips_to_database(conn, trips)
        
        # Step 4: Print summary
        print_trip_summary(conn, trip_config)
        
        print("✓ Trip detection complete!")
        
    except Exception as e:
        print(f"\n✗ Error during trip detection: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
