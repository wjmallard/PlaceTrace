#!/usr/bin/env python3
"""
Database configuration and operations for PlaceTrace
Handles connections to both main database and OSM boundaries database
Provides geocoding and location management functions
"""

import psycopg
from psycopg.rows import dict_row

from placetrace.config import config

# ============================================================================
# Database Connections
# ============================================================================

def get_main_connection():
    """Create connection to main database - uses .pgpass"""
    conn = psycopg.connect(
        dbname=config['databases']['main'],
        row_factory=dict_row
    )
    # Set session timezone to UTC to prevent timezone confusion
    conn.execute("SET timezone = 'UTC'")
    return conn

def get_osm_connection():
    """Create connection to OSM boundaries database - uses .pgpass"""
    return psycopg.connect(
        dbname=config['databases']['osm'],
        row_factory=dict_row
    )

# ============================================================================
# Geocoding Functions
# ============================================================================

def geocode_point(lat, lon):
    """
    Reverse geocode coordinates to full OSM administrative hierarchy.
    
    Args:
        lat: Latitude
        lon: Longitude
    
    Returns:
        dict: {
            'city': str or None,           # admin_level 8
            'county': str or None,         # admin_level 6
            'state': str or None,          # admin_level 4
            'country': str or None,        # admin_level 2
            'city_osm_id': int or None,
            'county_osm_id': int or None,
            'state_osm_id': int or None,
            'country_osm_id': int or None,
            'city_centroid': (lon, lat) or None,
            'county_centroid': (lon, lat) or None,
            'state_centroid': (lon, lat) or None,
            'country_centroid': (lon, lat) or None,
        }
        None: If no boundaries contain this point
    """
    conn = get_osm_connection()

    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT
                name,
                name_en,
                admin_level,
                osm_id,
                ST_X(ST_Centroid(geom)) as centroid_lon,
                ST_Y(ST_Centroid(geom)) as centroid_lat
            FROM admin_boundaries
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
              AND admin_level IN (2, 4, 6, 8)
            ORDER BY admin_level DESC
        """, (lon, lat))

        results = cursor.fetchall()

    conn.close()

    if not results:
        return None

    # Build hierarchy dictionary
    location = {
        'city': None,
        'county': None,
        'state': None,
        'country': None,
        'city_osm_id': None,
        'county_osm_id': None,
        'state_osm_id': None,
        'country_osm_id': None,
        'city_centroid': None,
        'county_centroid': None,
        'state_centroid': None,
        'country_centroid': None,
    }

    for r in results:
        name = r['name_en'] or r['name']
        osm_id = r['osm_id']
        centroid = (r['centroid_lon'], r['centroid_lat'])

        if r['admin_level'] == 8:  # City
            location['city'] = name
            location['city_osm_id'] = osm_id
            location['city_centroid'] = centroid
        elif r['admin_level'] == 6:  # County
            location['county'] = name
            location['county_osm_id'] = osm_id
            location['county_centroid'] = centroid
        elif r['admin_level'] == 4:  # State
            location['state'] = name
            location['state_osm_id'] = osm_id
            location['state_centroid'] = centroid
        elif r['admin_level'] == 2:  # Country
            location['country'] = name
            location['country_osm_id'] = osm_id
            location['country_centroid'] = centroid

    return location

# ============================================================================
# Location Table Operations
# ============================================================================

def get_or_create_location(conn, location_info):
    """
    Get existing location_id or create new location entry.
    
    Args:
        conn: Database connection to main database
        location_info: Dict from geocode_point() with hierarchy info
    
    Returns:
        int: location_id from Locations table
        None: If location_info is None or invalid
    
    Example:
        >>> info = geocode_point(37.4419, -122.1430)
        >>> location_id = get_or_create_location(conn, info)
        42
    """
    if not location_info or not location_info.get('country'):
        return None  # Must at least have country
    
    # Determine which admin level to use as primary
    # Prefer: city > county > state > country
    primary_centroid = (location_info['city_centroid'] or 
                       location_info['county_centroid'] or 
                       location_info['state_centroid'] or 
                       location_info['country_centroid'])
    
    # Determine admin_level
    if location_info['city']:
        admin_level = 8
    elif location_info['county']:
        admin_level = 6
    elif location_info['state']:
        admin_level = 4
    else:
        admin_level = 2
    
    with conn.cursor() as cursor:
        # Check if location exists
        # Use IS NOT DISTINCT FROM for NULL-safe comparison
        cursor.execute("""
            SELECT id FROM Locations
            WHERE (city IS NOT DISTINCT FROM %s)
              AND (county IS NOT DISTINCT FROM %s)
              AND (state IS NOT DISTINCT FROM %s)
              AND country = %s
        """, (location_info['city'], 
              location_info['county'],
              location_info['state'], 
              location_info['country']))
        
        existing = cursor.fetchone()
        if existing:
            return existing['id']
        
        # Insert new location with full hierarchy
        cursor.execute("""
            INSERT INTO Locations (
                city, county, state, country,
                city_osm_id, county_osm_id, state_osm_id, country_osm_id,
                admin_level, centroid
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
            RETURNING id
        """, (
            location_info['city'],
            location_info['county'],
            location_info['state'],
            location_info['country'],
            location_info['city_osm_id'],
            location_info['county_osm_id'],
            location_info['state_osm_id'],
            location_info['country_osm_id'],
            admin_level,
            primary_centroid[0],  # lon
            primary_centroid[1]   # lat
        ))
        
        new_location = cursor.fetchone()
        conn.commit()
        return new_location['id']
