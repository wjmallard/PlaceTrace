#!/usr/bin/env python3
"""
Interactive Location Finder
Find and add home or work locations for specific date ranges

Usage:
    python find_location.py home 2018-01-01 2019-12-31
    python find_location.py work 2013-02-01 2016-07-01
"""

import json
import sys
from pathlib import Path
from datetime import datetime, date

# Import database module
from db import get_main_connection


def find_location_candidates(conn, location_type, start_date, end_date, min_hours=100):
    """
    Find candidate locations for a date range.
    Returns locations ranked by time spent.
    """
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            place_id,
            ST_Y(location::geometry) as lat,
            ST_X(location::geometry) as lon,
            COUNT(*) as visit_count,
            SUM(duration_minutes) / 60.0 as total_hours,
            MIN(start_time)::date as first_visit,
            MAX(end_time)::date as last_visit,
            semantic_type,
            location_id
        FROM Visits
        WHERE start_time::date >= %s
          AND end_time::date <= %s
          AND place_id IS NOT NULL
        GROUP BY place_id, lat, lon, semantic_type, location_id
        HAVING SUM(duration_minutes) / 60.0 >= %s
        ORDER BY total_hours DESC
        LIMIT 20
    """, (start_date, end_date, min_hours))
    
    results = cursor.fetchall()
    cursor.close()
    
    return results


def get_location_name(conn, location_id, lat, lon):
    """
    Get location name from Locations table.
    Falls back to coordinates if not found.
    """
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


def format_location_json(location_type, place_id, lat, lon, name, start_date, end_date):
    """Format as JSON for home_locations.json or work_locations.json"""
    return {
        "place_id": place_id,
        "lat": lat,
        "lon": lon,
        "name": name,
        "start_date": start_date.isoformat() if isinstance(start_date, date) else start_date,
        "end_date": end_date.isoformat() if isinstance(end_date, (date, datetime)) and end_date else "2099-12-31"
    }


def parse_date(date_str):
    """Parse date string in various formats"""
    formats = ['%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%m-%d-%Y']
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {date_str}")


def interactive_mode(location_type):
    """Interactive mode with prompts for date range"""
    print("="*80)
    if location_type == 'home':
        print("🏠 INTERACTIVE HOME LOCATION FINDER")
    else:
        print("💼 INTERACTIVE WORK LOCATION FINDER")
    print("="*80)
    print()
    print(f"Find the {location_type} location for a specific date range.")
    print("Useful for filling gaps or correcting auto-detected locations.")
    print()
    
    # Get date range
    print("Enter date range (YYYY-MM-DD format):")
    
    while True:
        try:
            start_str = input("  Start date: ").strip()
            start_date = parse_date(start_str)
            break
        except ValueError as e:
            print(f"  Error: {e}. Try again.")
    
    while True:
        try:
            end_str = input("  End date (or 'present'): ").strip()
            if end_str.lower() in ['present', 'now', '']:
                end_date = date.today()
                is_present = True
            else:
                end_date = parse_date(end_str)
                is_present = False
            break
        except ValueError as e:
            print(f"  Error: {e}. Try again.")
    
    return start_date, end_date, is_present


def main():
    # Parse command line arguments
    if len(sys.argv) < 2:
        print("Usage: python find_location.py [home|work] [start_date] [end_date]")
        print()
        print("Examples:")
        print("  python find_location.py home                    # Interactive mode")
        print("  python find_location.py work 2018-01-01 2020-12-31")
        print("  python find_location.py home 2015-06-01 present")
        sys.exit(1)
    
    location_type = sys.argv[1].lower()
    if location_type not in ['home', 'work']:
        print("Error: Location type must be 'home' or 'work'")
        sys.exit(1)
    
    # Determine config file
    config_file = f"data/{location_type}_locations.json"
    emoji = "🏠" if location_type == "home" else "💼"
    
    # Get date range (from args or interactive)
    if len(sys.argv) >= 4:
        # Command line mode
        try:
            start_date = parse_date(sys.argv[2])
            end_str = sys.argv[3]
            if end_str.lower() in ['present', 'now']:
                end_date = date.today()
                is_present = True
            else:
                end_date = parse_date(end_str)
                is_present = False
        except ValueError as e:
            print(f"Error parsing dates: {e}")
            sys.exit(1)
    else:
        # Interactive mode
        start_date, end_date, is_present = interactive_mode(location_type)
    
    if end_date < start_date:
        print("\n✗ Error: End date must be after start date.")
        sys.exit(1)
    
    days = (end_date - start_date).days
    print(f"\n📅 Searching {days} days: {start_date} to {end_date}")
    print()
    
    # Connect to database
    conn = get_main_connection()
    
    try:
        # Find candidates
        print("🔍 Finding candidate locations...")
        candidates = find_location_candidates(conn, location_type, start_date, end_date, min_hours=100)
        
        if not candidates:
            print("\n✗ No locations found with 100+ hours in this period.")
            print("   Try a wider date range or lower the threshold.")
            return
        
        # Display candidates
        print()
        print("="*80)
        print(f"CANDIDATE {location_type.upper()} LOCATIONS ({len(candidates)} found)")
        print("="*80)
        print()
        
        for i, candidate in enumerate(candidates, 1):
            location_name = get_location_name(
                conn, 
                candidate['location_id'],
                candidate['lat'],
                candidate['lon']
            )
            
            print(f"{i}. {location_name}")
            print(f"   Total time: {candidate['total_hours']:.0f} hours ({candidate['total_hours']/24:.1f} days)")
            print(f"   Visits: {candidate['visit_count']}")
            print(f"   Type: {candidate['semantic_type'] or 'Unknown'}")
            print(f"   Coordinates: ({candidate['lat']:.4f}, {candidate['lon']:.4f})")
            print(f"   Date range: {candidate['first_visit']} to {candidate['last_visit']}")
            if candidate['place_id']:
                print(f"   Place ID: {candidate['place_id']}")
            print()
        
        print("="*80)
        print()
        
        # Select one
        while True:
            try:
                choice = input(f"Select {location_type} location (1-{len(candidates)}, or 'q' to quit): ").strip()
                if choice.lower() == 'q':
                    print("\nCancelled.")
                    return
                
                choice_num = int(choice)
                if 1 <= choice_num <= len(candidates):
                    selected = candidates[choice_num - 1]
                    break
                else:
                    print(f"  Please enter a number between 1 and {len(candidates)}")
            except ValueError:
                print("  Invalid input. Please enter a number or 'q'.")
        
        # Get location name
        location_name = get_location_name(
            conn,
            selected['location_id'],
            selected['lat'],
            selected['lon']
        )
        print(f"\n✓ Selected: {location_name}")
        print()
        
        # Generate JSON
        location_entry = format_location_json(
            location_type,
            selected['place_id'],
            selected['lat'],
            selected['lon'],
            location_name,
            start_date,
            end_date if not is_present else None
        )
        
        print("="*80)
        print(f"JSON ENTRY FOR {config_file}:")
        print("="*80)
        print()
        print(json.dumps(location_entry, indent=2))
        print()
        print("="*80)
        print()
        
        # Option to add directly
        response = input(f"Add this entry to {config_file} now? (y/n): ").strip().lower()
        
        if response in ['y', 'yes']:
            # Load existing locations
            config_path = Path(config_file)
            if config_path.exists():
                with open(config_path, 'r') as f:
                    locations = json.load(f)
            else:
                locations = []
            
            # Add new entry
            locations.append(location_entry)
            
            # Sort by start date
            locations.sort(key=lambda h: h['start_date'])
            
            # Save
            with open(config_path, 'w') as f:
                json.dump(locations, f, indent=2)
            
            print(f"\n✓ Added to {config_file}")
            print(f"  Total {location_type} locations: {len(locations)}")
        else:
            print("\nNot saved. Copy the JSON above to add manually.")
        
        print()
        
    finally:
        conn.close()


if __name__ == '__main__':
    main()
