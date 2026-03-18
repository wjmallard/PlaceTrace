#!/usr/bin/env python3
"""
Location Manager
Auto-detect and manage home/work locations that change over time

Usage:
    python manage_locations.py --detect home    # Auto-detect all homes
    python manage_locations.py --detect work    # Auto-detect all workplaces
    python manage_locations.py --list           # List current locations
"""

import json
import sys
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

from placetrace.db import get_main_connection
from placetrace.config import project_root


def load_locations(location_type):
    """Load locations from JSON file"""
    config_file = project_root / "data" / f"{location_type}_locations.json"
    if config_file.exists():
        with open(config_file, 'r') as f:
            data = json.load(f)
            # Convert date strings to date objects
            locations = []
            for loc in data:
                loc['start_date'] = date.fromisoformat(loc['start_date']) if loc['start_date'] else None
                loc['end_date'] = date.fromisoformat(loc['end_date']) if loc['end_date'] else None
                locations.append(loc)
            return locations
    return []


def save_locations(location_type, locations):
    """Save locations to JSON file"""
    config_file = project_root / "data" / f"{location_type}_locations.json"
    
    # Convert date objects to strings for JSON
    data = []
    for loc in locations:
        loc_copy = loc.copy()
        loc_copy['start_date'] = loc['start_date'].isoformat() if loc['start_date'] else None
        loc_copy['end_date'] = loc['end_date'].isoformat() if loc['end_date'] else "2099-12-31"
        data.append(loc_copy)
    
    with open(config_file, 'w') as f:
        json.dump(data, f, indent=2)


def get_location_name(conn, location_id, lat, lon):
    """Get location name from Locations table"""
    if not location_id:
        return f"({lat:.4f}, {lon:.4f})"
    
    cursor = conn.cursor()
    cursor.execute("""
        SELECT city, state, country
        FROM Locations
        WHERE id = %s
    """, (location_id,))
    
    result = cursor.fetchone()
    cursor.close()
    
    if not result:
        return f"({lat:.4f}, {lon:.4f})"
    
    # Format location name
    if result['city'] and result['state']:
        return f"{result['city']}, {result['state']}"
    elif result['city']:
        return f"{result['city']}, {result['country']}"
    elif result['state']:
        return f"{result['state']}, {result['country']}"
    else:
        return result['country']


def auto_detect_homes(conn, min_months=2):
    """
    Auto-detect home locations from visit data.
    Finds locations where user spent significant time, excluding known work locations.
    """
    # Load work locations to exclude them
    work_locations = load_locations('work')
    work_place_ids = {w['place_id'] for w in work_locations if w.get('place_id')}
    
    if work_place_ids:
        print(f"   Excluding {len(work_place_ids)} known work location(s)")
    
    cursor = conn.cursor()
    
    # Get all high-duration visits grouped by location and month
    if work_place_ids:
        cursor.execute("""
            SELECT 
                place_id,
                DATE_TRUNC('month', start_time)::date as month,
                ST_Y(location::geometry) as lat,
                ST_X(location::geometry) as lon,
                location_id,
                COUNT(*) as visit_count,
                SUM(duration_minutes) as total_minutes
            FROM Visits
            WHERE place_id IS NOT NULL
              AND place_id != ALL(%s)
            GROUP BY place_id, DATE_TRUNC('month', start_time), lat, lon, location_id
            HAVING SUM(duration_minutes) > 5000  -- 83+ hours per month
            ORDER BY month, total_minutes DESC
        """, (list(work_place_ids),))
    else:
        cursor.execute("""
            SELECT 
                place_id,
                DATE_TRUNC('month', start_time)::date as month,
                ST_Y(location::geometry) as lat,
                ST_X(location::geometry) as lon,
                location_id,
                COUNT(*) as visit_count,
                SUM(duration_minutes) as total_minutes
            FROM Visits
            WHERE place_id IS NOT NULL
            GROUP BY place_id, DATE_TRUNC('month', start_time), lat, lon, location_id
            HAVING SUM(duration_minutes) > 5000  -- 83+ hours per month
            ORDER BY month, total_minutes DESC
        """)
    
    monthly_data = cursor.fetchall()
    cursor.close()
    
    # Group by place_id to find continuous residency periods
    place_periods = defaultdict(list)
    
    for row in monthly_data:
        place_periods[row['place_id']].append({
            'month': row['month'],
            'lat': row['lat'],
            'lon': row['lon'],
            'location_id': row['location_id'],
            'visit_count': row['visit_count'],
            'total_hours': row['total_minutes'] / 60
        })
    
    # Find continuous periods (homes)
    detected_homes = []
    
    for place_id, months in place_periods.items():
        if len(months) < min_months:
            continue
        
        # Sort by month
        months.sort(key=lambda m: m['month'])
        
        # Find continuous periods (gap of >2 months = different residency)
        current_period = [months[0]]
        
        for i in range(1, len(months)):
            prev_month = current_period[-1]['month']
            curr_month = months[i]['month']
            
            # Calculate month difference
            month_diff = ((curr_month.year - prev_month.year) * 12 + 
                         (curr_month.month - prev_month.month))
            
            if month_diff <= 2:  # Allow 1-month gap (e.g., vacation)
                current_period.append(months[i])
            else:
                # Save current period as a home
                if len(current_period) >= min_months:
                    home = finalize_period(conn, place_id, current_period)
                    if home:
                        detected_homes.append(home)
                
                # Start new period
                current_period = [months[i]]
        
        # Don't forget the last period
        if len(current_period) >= min_months:
            home = finalize_period(conn, place_id, current_period)
            if home:
                detected_homes.append(home)
    
    # Sort by start date
    detected_homes.sort(key=lambda h: h['start_date'])
    
    return detected_homes


def auto_detect_work(conn, min_months=2):
    """
    Auto-detect work locations from visit data.
    Finds locations labeled 'Work' where user spent significant time.
    """
    cursor = conn.cursor()
    
    # Get all work visits grouped by location and month
    cursor.execute("""
        SELECT 
            place_id,
            DATE_TRUNC('month', start_time)::date as month,
            ST_Y(location::geometry) as lat,
            ST_X(location::geometry) as lon,
            location_id,
            COUNT(*) as visit_count,
            SUM(duration_minutes) as total_minutes
        FROM Visits
        WHERE place_id IS NOT NULL
          AND semantic_type = 'Work'
        GROUP BY place_id, DATE_TRUNC('month', start_time), lat, lon, location_id
        HAVING SUM(duration_minutes) > 2000  -- 33+ hours per month
        ORDER BY month, total_minutes DESC
    """)
    
    monthly_data = cursor.fetchall()
    cursor.close()
    
    # Group by place_id to find continuous employment periods
    place_periods = defaultdict(list)
    
    for row in monthly_data:
        place_periods[row['place_id']].append({
            'month': row['month'],
            'lat': row['lat'],
            'lon': row['lon'],
            'location_id': row['location_id'],
            'visit_count': row['visit_count'],
            'total_hours': row['total_minutes'] / 60
        })
    
    # Find continuous periods (jobs)
    detected_work = []
    
    for place_id, months in place_periods.items():
        if len(months) < min_months:
            continue
        
        # Sort by month
        months.sort(key=lambda m: m['month'])
        
        # Find continuous periods (gap of >2 months = different job)
        current_period = [months[0]]
        
        for i in range(1, len(months)):
            prev_month = current_period[-1]['month']
            curr_month = months[i]['month']
            
            # Calculate month difference
            month_diff = ((curr_month.year - prev_month.year) * 12 + 
                         (curr_month.month - prev_month.month))
            
            if month_diff <= 2:  # Allow 1-month gap
                current_period.append(months[i])
            else:
                # Save current period as a workplace
                if len(current_period) >= min_months:
                    work = finalize_period(conn, place_id, current_period)
                    if work:
                        detected_work.append(work)
                
                # Start new period
                current_period = [months[i]]
        
        # Don't forget the last period
        if len(current_period) >= min_months:
            work = finalize_period(conn, place_id, current_period)
            if work:
                detected_work.append(work)
    
    # Sort by start date
    detected_work.sort(key=lambda w: w['start_date'])
    
    return detected_work


def finalize_period(conn, place_id, monthly_data):
    """
    Determine precise start and end dates for a location period.
    Looks at actual visit dates rather than just month boundaries.
    """
    first_month = monthly_data[0]['month']
    last_month = monthly_data[-1]['month']
    
    cursor = conn.cursor()
    
    # Query for actual visit dates at this location
    cursor.execute("""
        SELECT 
            MIN(start_time)::date as first_visit,
            MAX(end_time)::date as last_visit
        FROM Visits
        WHERE place_id = %s
          AND start_time::date >= %s
          AND start_time::date <= %s + INTERVAL '1 month'
    """, (place_id, first_month, last_month))
    
    result = cursor.fetchone()
    cursor.close()
    
    if not result or not result['first_visit']:
        return None
    
    return {
        'place_id': place_id,
        'lat': monthly_data[0]['lat'],
        'lon': monthly_data[0]['lon'],
        'location_id': monthly_data[0]['location_id'],
        'start_date': result['first_visit'],
        'end_date': result['last_visit'],
        'months': len(monthly_data),
        'total_hours': sum(m['total_hours'] for m in monthly_data)
    }


def review_and_save_detected(conn, location_type, detected_locations):
    """Interactive review of detected locations"""
    emoji = "🏠" if location_type == "home" else "💼"
    
    # Load existing locations to check for overlaps
    existing_locations = load_locations(location_type)
    
    print("\n" + "="*80)
    print(f"{emoji} DETECTED {location_type.upper()} LOCATIONS")
    print("="*80)
    print()
    
    if not detected_locations:
        print(f"No {location_type} locations detected. Try lowering min_months parameter.")
        return
    
    print(f"Found {len(detected_locations)} potential {location_type} locations:\n")
    
    for i, loc in enumerate(detected_locations, 1):
        location_name = get_location_name(conn, loc['location_id'], loc['lat'], loc['lon'])
        
        # Check if this period overlaps with existing configured locations
        matching_config = None
        for existing in existing_locations:
            # Check date overlap
            existing_start = existing['start_date']
            existing_end = existing['end_date'] if existing['end_date'] else date.max
            detected_start = loc['start_date']
            detected_end = loc['end_date']
            
            # Check if dates overlap
            if not (detected_end < existing_start or detected_start > existing_end):
                # Check if same location (within 1km)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT ST_Distance(
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                    ) / 1000 as distance_km
                """, (loc['lon'], loc['lat'], existing['lon'], existing['lat']))
                result = cursor.fetchone()
                cursor.close()
                
                if result['distance_km'] < 1:  # Same location
                    matching_config = existing
                    break
        
        # Print with annotation showing which config entry it matches
        if matching_config:
            status = f"✓ Matches: {matching_config['name']}"
        else:
            status = "⚠ Not configured"
        
        print(f"{i}. {location_name} [{status}]")
        print(f"   Period: {loc['start_date']} to {loc['end_date']}")
        print(f"   Duration: {loc['months']} months")
        print(f"   Time spent: {loc['total_hours']:.0f} hours")
        print(f"   Coordinates: ({loc['lat']:.4f}, {loc['lon']:.4f})")
        print()
    
    print("="*80)
    
    # Count how many are new vs already configured
    configured_count = sum(1 for loc in detected_locations 
                          if any(is_same_location_period(conn, loc, existing) 
                                for existing in existing_locations))
    new_count = len(detected_locations) - configured_count
    
    print(f"\nSummary: {new_count} new, {configured_count} already configured")
    print()
    
    if existing_locations:
        print("⚠️  WARNING: Saving will REPLACE your existing config file!")
        print(f"   Current config has {len(existing_locations)} entries.")
        print()
    
    response = input(f"Save these {location_type} locations to config? (y/N): ").strip().lower()
    
    if response in ['y', 'yes']:
        locations = []
        for loc in detected_locations:
            location_name = get_location_name(conn, loc['location_id'], loc['lat'], loc['lon'])
            
            locations.append({
                'place_id': loc['place_id'],
                'lat': loc['lat'],
                'lon': loc['lon'],
                'name': location_name,
                'start_date': loc['start_date'],
                'end_date': loc['end_date']
            })
        
        save_locations(location_type, locations)
        print(f"\n✓ Saved {len(locations)} {location_type} locations to {location_type}_locations.json")
    else:
        print("\nNot saved. Use find_location.py to add individual entries manually.")


def is_same_location_period(conn, detected, existing):
    """
    Check if detected location overlaps with existing configured location.
    Returns True if same location and overlapping time period.
    """
    # Check date overlap
    existing_start = existing['start_date']
    existing_end = existing['end_date'] if existing['end_date'] else date.max
    detected_start = detected['start_date']
    detected_end = detected['end_date']
    
    # No date overlap
    if detected_end < existing_start or detected_start > existing_end:
        return False
    
    # Check if same location (within 1km)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ST_Distance(
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
        ) / 1000 as distance_km
    """, (detected['lon'], detected['lat'], existing['lon'], existing['lat']))
    result = cursor.fetchone()
    cursor.close()
    
    return result['distance_km'] < 1  # Same location if within 1km


def list_locations(location_type):
    """List current locations from config file"""
    locations = load_locations(location_type)
    emoji = "🏠" if location_type == "home" else "💼"
    
    print("\n" + "="*80)
    print(f"{emoji} CONFIGURED {location_type.upper()} LOCATIONS")
    print("="*80)
    
    if not locations:
        print(f"\nNo {location_type} locations configured yet.")
        print(f"Run: python manage_locations.py --detect {location_type}")
    else:
        print()
        for i, loc in enumerate(locations, 1):
            end_str = loc['end_date'].isoformat() if loc['end_date'] else 'present'
            print(f"{i}. {loc['name']}")
            print(f"   Period: {loc['start_date'].isoformat()} to {end_str}")
            print(f"   Location: ({loc['lat']:.4f}, {loc['lon']:.4f})")
            if loc.get('place_id'):
                print(f"   Place ID: {loc['place_id']}")
            print()
    
    print("="*80)


def main():
    if len(sys.argv) < 2:
        print("Usage: python manage_locations.py [--detect home|work] [--list]")
        print()
        print("Commands:")
        print("  --detect home      Auto-detect all home locations")
        print("  --detect work      Auto-detect all work locations")
        print("  --list             List all configured locations")
        print()
        print("Examples:")
        print("  python manage_locations.py --detect home")
        print("  python manage_locations.py --detect work")
        print("  python manage_locations.py --list")
        sys.exit(0)
    
    command = sys.argv[1]
    
    if command == '--detect':
        if len(sys.argv) < 3:
            print("Error: --detect requires location type (home or work)")
            sys.exit(1)
        
        location_type = sys.argv[2].lower()
        if location_type not in ['home', 'work']:
            print("Error: Location type must be 'home' or 'work'")
            sys.exit(1)
        
        print("="*80)
        if location_type == 'home':
            print("🏠 AUTO-DETECT HOME LOCATIONS")
        else:
            print("💼 AUTO-DETECT WORK LOCATIONS")
        print("="*80)
        print()
        print("Analyzing location history to find continuous residency/employment periods...")
        print()
        
        conn = get_main_connection()
        
        try:
            if location_type == 'home':
                detected = auto_detect_homes(conn, min_months=2)
            else:
                detected = auto_detect_work(conn, min_months=2)
            
            if detected:
                review_and_save_detected(conn, location_type, detected)
            else:
                print(f"\nNo {location_type} locations detected.")
                print("Try adjusting the min_months parameter in the code.")
        
        finally:
            conn.close()
    
    elif command == '--list':
        print("="*80)
        print("LOCATION CONFIGURATION")
        print("="*80)
        
        list_locations('home')
        list_locations('work')
    
    else:
        print(f"Unknown command: {command}")
        print("Use --detect or --list")
        sys.exit(1)


if __name__ == '__main__':
    main()
