"""
Visits API endpoints
GET /api/visits - List visits with filtering
GET /api/spots - List unique visit locations with aggregate stats
"""

from datetime import datetime

from flask import Blueprint, request, jsonify

from placetrace.web.database import get_db
from placetrace.web.serialize import VISIT_COLUMNS, format_location_name, visit_dict

bp = Blueprint('visits', __name__)


def parse_visit_filters():
    """
    Build SQL filter fragments from the request's query parameters.

    Shared by /visits and /spots, which accept the same filters:
        - date: Single date (YYYY-MM-DD) - convenient shorthand for full day
        - bbox: Bounding box as 'min_lat,min_lng,max_lat,max_lng'
        - lat, lon, radius_km: Radius-based spatial filter (alternative to bbox)
        - start_date: YYYY-MM-DD (local dates) or ISO datetime (UTC), inclusive - ignored if date is provided
        - end_date: YYYY-MM-DD (local dates) or ISO datetime (UTC), inclusive - ignored if date is provided
        - location_id: Filter by location ID
        - trip_id: Filter by trip ID
        - limit: Maximum results (default 1000)
        - offset: Pagination offset (default 0)

    Returns (error_response, joins, where, params, filters); error_response is
    None unless a parameter failed validation.
    """
    date_param = request.args.get('date')
    bbox = request.args.get('bbox')
    lat = request.args.get('lat', type=float)
    lon = request.args.get('lon', type=float)
    radius_km = request.args.get('radius_km', type=float)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    location_id = request.args.get('location_id', type=int)
    trip_id = request.args.get('trip_id', type=int)
    limit = request.args.get('limit', type=int, default=1000)
    offset = request.args.get('offset', type=int, default=0)

    if limit < 1 or limit > 10000:
        error = jsonify({'error': 'limit must be between 1 and 10000', 'status': 400}), 400
        return error, None, None, None, None

    joins = []
    where = []
    params = {
        'limit': limit,
        'offset': offset,
    }
    filters = {}

    # Single-date shorthand: the visit overlaps this date if
    # local_start_date <= date AND local_end_date >= date
    # (overrides start_date/end_date if both are provided)
    if date_param:
        try:
            date_obj = datetime.strptime(date_param, '%Y-%m-%d').date()
        except ValueError:
            error = jsonify({
                'error': 'Invalid date format. Use YYYY-MM-DD (e.g., 2024-12-01)',
                'status': 400
            }), 400
            return error, None, None, None, None
        where.append("v.local_start_date <= %(date)s")
        where.append("v.local_end_date >= %(date)s")
        params['date'] = date_obj
        filters['date'] = date_param
        start_date = None
        end_date = None

    # Bounding box filter
    if bbox:
        try:
            min_lat, min_lng, max_lat, max_lng = map(float, bbox.split(','))
        except (ValueError, TypeError):
            error = jsonify({'error': 'Invalid bbox format: expected min_lat,min_lng,max_lat,max_lng', 'status': 400}), 400
            return error, None, None, None, None
        where.append("ST_Intersects(v.location, ST_MakeEnvelope(%(min_lng)s, %(min_lat)s, %(max_lng)s, %(max_lat)s, 4326))")
        params.update({
            'min_lat': min_lat,
            'min_lng': min_lng,
            'max_lat': max_lat,
            'max_lng': max_lng,
        })
        filters['bbox'] = bbox

    # Radius-based spatial filter (alternative to bbox)
    if lat is not None and lon is not None and radius_km is not None:
        where.append("""ST_DWithin(
            v.location,
            ST_SetSRID(ST_MakePoint(%(center_lon)s, %(center_lat)s), 4326)::geography,
            %(radius_m)s
        )""")
        params.update({
            'center_lat': lat,
            'center_lon': lon,
            'radius_m': radius_km * 1000,
        })
        filters['spatial'] = f'{lat},{lon} within {radius_km}km'

    # Date range filters (YYYY-MM-DD strings match local dates, ISO timestamps match UTC)
    if start_date:
        try:
            if len(start_date) == 10 and start_date.count('-') == 2:
                params['range_start_date'] = datetime.strptime(start_date, '%Y-%m-%d').date()
                where.append("v.local_end_date >= %(range_start_date)s")
            else:
                params['range_start_dt'] = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                where.append("v.start_time >= %(range_start_dt)s")
            filters['start_date'] = start_date
        except (ValueError, AttributeError) as e:
            error = jsonify({'error': f'Invalid start_date format: {str(e)}', 'status': 400}), 400
            return error, None, None, None, None

    if end_date:
        try:
            if len(end_date) == 10 and end_date.count('-') == 2:
                params['range_end_date'] = datetime.strptime(end_date, '%Y-%m-%d').date()
                where.append("v.local_start_date <= %(range_end_date)s")
            else:
                params['range_end_dt'] = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                where.append("v.end_time <= %(range_end_dt)s")
            filters['end_date'] = end_date
        except (ValueError, AttributeError) as e:
            error = jsonify({'error': f'Invalid end_date format: {str(e)}', 'status': 400}), 400
            return error, None, None, None, None

    if location_id:
        where.append("v.location_id = %(location_id)s")
        params['location_id'] = location_id
        filters['location_id'] = location_id

    if trip_id:
        joins.append("JOIN Trip_Visits tv ON tv.visit_id = v.id")
        where.append("tv.trip_id = %(trip_id)s")
        params['trip_id'] = trip_id
        filters['trip_id'] = trip_id

    return None, joins, where, params, filters


@bp.route('/visits')
def get_visits():
    """
    Get visits with optional filters (see parse_visit_filters for parameters)

    Returns:
        JSON response with visits array, count, and filters_applied
    """
    error, joins, where, params, filters = parse_visit_filters()
    if error:
        return error

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                {VISIT_COLUMNS}
            FROM Visits v
            LEFT JOIN Locations l ON l.id = v.location_id
            {" ".join(joins)}
            {where_sql}
            ORDER BY v.start_time DESC, v.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """, params)
        visits = cursor.fetchall()

    return jsonify({
        'visits': [visit_dict(row) for row in visits],
        'count': len(visits),
        'filters_applied': filters,
    })


@bp.route('/spots')
def get_spots():
    """
    Get unique visit locations (spots) with aggregate stats, for map display.

    Accepts the same filters as /api/visits, but the limit applies to unique
    locations rather than individual visits, so one frequently visited place
    cannot crowd everything else out of the viewport. Spots are ordered by
    visit count, so capping keeps the most significant places.

    Returns:
        JSON response with spots array, count, total_visits, and filters_applied
    """
    error, joins, where, params, filters = parse_visit_filters()
    if error:
        return error

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                s.latitude,
                s.longitude,
                s.visit_count,
                s.total_minutes,
                s.first_visit,
                s.last_visit,
                s.last_local_date,
                l.city,
                l.county,
                l.state,
                l.country
            FROM (
                SELECT
                    ROUND(ST_Y(v.location::geometry)::numeric, 6)::float8 AS latitude,
                    ROUND(ST_X(v.location::geometry)::numeric, 6)::float8 AS longitude,
                    count(*) AS visit_count,
                    sum(v.duration_minutes) AS total_minutes,
                    min(v.start_time) AS first_visit,
                    max(v.end_time) AS last_visit,
                    max(v.local_start_date) AS last_local_date,
                    max(v.location_id) AS location_id
                FROM Visits v
                {" ".join(joins)}
                {where_sql}
                GROUP BY 1, 2
                ORDER BY count(*) DESC
                LIMIT %(limit)s OFFSET %(offset)s
            ) s
            LEFT JOIN Locations l ON l.id = s.location_id
            ORDER BY s.visit_count DESC, s.latitude, s.longitude
        """, params)
        spots = cursor.fetchall()

    return jsonify({
        'spots': [
            {
                'latitude': row['latitude'],
                'longitude': row['longitude'],
                'visit_count': row['visit_count'],
                'total_minutes': row['total_minutes'],
                'first_visit': row['first_visit'].isoformat() if row['first_visit'] else None,
                'last_visit': row['last_visit'].isoformat() if row['last_visit'] else None,
                'last_local_date': row['last_local_date'].isoformat() if row['last_local_date'] else None,
                'location_name': format_location_name(row),
            }
            for row in spots
        ],
        'count': len(spots),
        'total_visits': sum(row['visit_count'] for row in spots),
        'filters_applied': filters,
    })
