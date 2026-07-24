"""
pt-manage-places — Auto-detect and manage home/work locations that change over time.

Usage:
    pt-manage-places --detect home    # Auto-detect all homes
    pt-manage-places --detect work    # Auto-detect all workplaces
    pt-manage-places --list           # List current locations
"""

import argparse
import json
from datetime import date
from collections import defaultdict

from placetrace.db import get_location_name, get_main_connection
from placetrace.config import project_root
from placetrace.geo import haversine_km


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


def find_continuous_periods(conn, monthly_data, min_months):
    """
    Group monthly visit totals by place and split into continuous periods.
    A gap of more than 2 months starts a new period (1-month gaps are allowed,
    e.g. a vacation). Returns periods sorted by start date.
    """
    place_periods = defaultdict(list)

    for row in monthly_data:
        place_periods[row['place_id']].append({
            'month': row['month'],
            'lat': row['lat'],
            'lon': row['lon'],
            'location_id': row['location_id'],
            'visit_count': row['visit_count'],
            'total_hours': row['total_minutes'] / 60,
        })

    detected = []

    for place_id, months in place_periods.items():
        if len(months) < min_months:
            continue

        months.sort(key=lambda m: m['month'])
        current_period = [months[0]]

        for month in months[1:]:
            prev_month = current_period[-1]['month']
            month_diff = ((month['month'].year - prev_month.year) * 12 +
                          (month['month'].month - prev_month.month))

            if month_diff <= 2:
                current_period.append(month)
            else:
                if len(current_period) >= min_months:
                    period = finalize_period(conn, place_id, current_period)
                    if period:
                        detected.append(period)
                current_period = [month]

        # Don't forget the last period
        if len(current_period) >= min_months:
            period = finalize_period(conn, place_id, current_period)
            if period:
                detected.append(period)

    detected.sort(key=lambda p: p['start_date'])

    return detected


def auto_detect_homes(conn, min_months=2):
    """
    Auto-detect home locations from visit data.
    Finds locations where user spent significant time, excluding known work locations.
    """
    # Load work locations to exclude them
    work_locations = load_locations('work')
    work_place_ids = [w['place_id'] for w in work_locations if w.get('place_id')]

    if work_place_ids:
        print(f"   Excluding {len(work_place_ids)} known work location(s)")

    cursor = conn.cursor()

    # Get all high-duration visits grouped by location and month
    # (an empty exclusion list matches every place_id)
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
          AND place_id != ALL(%(work_place_ids)s)
        GROUP BY place_id, DATE_TRUNC('month', start_time), lat, lon, location_id
        HAVING SUM(duration_minutes) > 5000  -- 83+ hours per month
        ORDER BY month, total_minutes DESC
    """, {
        'work_place_ids': work_place_ids,
    })

    monthly_data = cursor.fetchall()
    cursor.close()

    return find_continuous_periods(conn, monthly_data, min_months)


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

    return find_continuous_periods(conn, monthly_data, min_months)


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
                if haversine_km(loc['lat'], loc['lon'], existing['lat'], existing['lon']) < 1:
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
                          if any(is_same_location_period(loc, existing)
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


def is_same_location_period(detected, existing):
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
    return haversine_km(detected['lat'], detected['lon'], existing['lat'], existing['lon']) < 1


def list_locations(location_type):
    """List current locations from config file"""
    locations = load_locations(location_type)
    emoji = "🏠" if location_type == "home" else "💼"

    print("\n" + "="*80)
    print(f"{emoji} CONFIGURED {location_type.upper()} LOCATIONS")
    print("="*80)

    if not locations:
        print(f"\nNo {location_type} locations configured yet.")
        print(f"Run: pt-manage-places --detect {location_type}")
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
    parser = argparse.ArgumentParser(description="Auto-detect and manage home/work locations.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--detect",
        choices=["home", "work"],
        help="auto-detect all home or work locations",
    )
    group.add_argument(
        "--list",
        action="store_true",
        help="list all configured locations",
    )
    args = parser.parse_args()

    if args.detect:
        location_type = args.detect

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

    else:
        print("="*80)
        print("LOCATION CONFIGURATION")
        print("="*80)

        list_locations('home')
        list_locations('work')


if __name__ == '__main__':
    main()
