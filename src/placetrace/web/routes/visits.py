"""
Visits API endpoint
GET /api/visits - List visits with filtering
"""

from datetime import datetime

from flask import Blueprint, request, jsonify

from placetrace.web.database import get_db
from placetrace.web.serialize import visit_dict

bp = Blueprint('visits', __name__)

VISIT_COLUMNS = """
    v.id,
    v.start_time,
    v.end_time,
    v.duration_minutes,
    v.local_start_date,
    v.local_start_time,
    v.local_end_date,
    v.local_end_time,
    ST_Y(v.location::geometry) AS latitude,
    ST_X(v.location::geometry) AS longitude,
    v.semantic_type,
    l.city,
    l.county,
    l.state,
    l.country
"""


@bp.route('/visits')
def get_visits():
    """
    Get visits with optional filters

    Query parameters:
        - date: Single date (YYYY-MM-DD) - convenient shorthand for full day
        - bbox: Bounding box as 'min_lat,min_lng,max_lat,max_lng'
        - lat, lon, radius_km: Radius-based spatial filter (alternative to bbox)
        - start_date: YYYY-MM-DD (local dates) or ISO datetime (UTC), inclusive - ignored if date is provided
        - end_date: YYYY-MM-DD (local dates) or ISO datetime (UTC), inclusive - ignored if date is provided
        - location_id: Filter by location ID
        - trip_id: Filter by trip ID
        - limit: Maximum results (default 1000)
        - offset: Pagination offset (default 0)

    Returns:
        JSON response with visits array, count, and filters_applied
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
        return jsonify({'error': 'limit must be between 1 and 10000', 'status': 400}), 400

    where = []
    joins = []
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
            return jsonify({
                'error': 'Invalid date format. Use YYYY-MM-DD (e.g., 2024-12-01)',
                'status': 400
            }), 400
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
            return jsonify({'error': 'Invalid bbox format: expected min_lat,min_lng,max_lat,max_lng', 'status': 400}), 400
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
            return jsonify({'error': f'Invalid start_date format: {str(e)}', 'status': 400}), 400

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
            return jsonify({'error': f'Invalid end_date format: {str(e)}', 'status': 400}), 400

    if location_id:
        where.append("v.location_id = %(location_id)s")
        params['location_id'] = location_id
        filters['location_id'] = location_id

    if trip_id:
        joins.append("JOIN Trip_Visits tv ON tv.visit_id = v.id")
        where.append("tv.trip_id = %(trip_id)s")
        params['trip_id'] = trip_id
        filters['trip_id'] = trip_id

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
