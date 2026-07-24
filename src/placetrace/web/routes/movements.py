"""
Movements API endpoints
GET /api/movements - List movements with filtering
GET /api/movements/<id> - Get movement detail with route geometry
"""

from datetime import datetime

from flask import Blueprint, request, jsonify

from placetrace.web.database import get_db

bp = Blueprint('movements', __name__)

MOVEMENT_COLUMNS = """
    m.id,
    m.start_time,
    m.end_time,
    m.duration_minutes,
    m.local_start_date,
    m.local_start_time,
    m.local_end_date,
    m.local_end_time,
    ST_Y(m.start_location::geometry) AS start_latitude,
    ST_X(m.start_location::geometry) AS start_longitude,
    ST_Y(m.end_location::geometry) AS end_latitude,
    ST_X(m.end_location::geometry) AS end_longitude,
    m.activity_type,
    m.movement_type,
    m.source,
    m.distance_meters,
    m.confidence,
    m.preceding_visit_id,
    m.following_visit_id
"""


def movement_times(row):
    """Serialize the shared temporal fields of a movement row."""
    return {
        'start_time': row['start_time'].isoformat() if row['start_time'] else None,
        'end_time': row['end_time'].isoformat() if row['end_time'] else None,
        'duration_minutes': row['duration_minutes'],
        'local_start_date': row['local_start_date'].isoformat() if row['local_start_date'] else None,
        'local_start_time': row['local_start_time'].isoformat() if row['local_start_time'] else None,
        'local_end_date': row['local_end_date'].isoformat() if row['local_end_date'] else None,
        'local_end_time': row['local_end_time'].isoformat() if row['local_end_time'] else None,
    }


@bp.route('/movements')
def get_movements():
    """
    Get movements for a specific date with optional filters

    Query parameters:
        - date: REQUIRED - Single date (YYYY-MM-DD) for which to fetch movements
        - bbox: Bounding box as 'min_lat,min_lng,max_lat,max_lng' (checks if route intersects)
        - activity_type: Filter by activity type (e.g., 'WALKING', 'CYCLING', 'IN_PASSENGER_VEHICLE')
        - movement_type: Filter by movement type ('activity', 'breadcrumb_trail')
        - source: Filter by data source ('google_timeline', 'strava', etc.)
        - trip_id: Filter by movements connecting visits in this trip
        - min_distance: Minimum distance in meters
        - max_distance: Maximum distance in meters
        - include_routes: Include route_geojson as GeoJSON LineString (default: false)
        - limit: Maximum results (default 1000)
        - offset: Pagination offset (default 0)

    Returns:
        JSON response with movements array, count, and filters_applied
    """
    # Date parameter is required
    date_param = request.args.get('date')
    if not date_param:
        return jsonify({
            'error': 'date parameter is required (format: YYYY-MM-DD)',
            'status': 400
        }), 400

    try:
        date_obj = datetime.strptime(date_param, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({
            'error': 'Invalid date format. Use YYYY-MM-DD (e.g., 2024-12-01)',
            'status': 400
        }), 400

    bbox = request.args.get('bbox')
    activity_type = request.args.get('activity_type')
    movement_type = request.args.get('movement_type')
    source = request.args.get('source')
    trip_id = request.args.get('trip_id', type=int)
    min_distance = request.args.get('min_distance', type=float)
    max_distance = request.args.get('max_distance', type=float)
    include_routes = request.args.get('include_routes', 'false').lower() == 'true'
    limit = request.args.get('limit', type=int, default=1000)
    offset = request.args.get('offset', type=int, default=0)

    if limit < 1 or limit > 10000:
        return jsonify({'error': 'limit must be between 1 and 10000', 'status': 400}), 400

    # Movement overlaps the date if local_start_date <= date AND local_end_date >= date
    where = [
        "m.local_start_date <= %(date)s",
        "m.local_end_date >= %(date)s",
    ]
    params = {
        'date': date_obj,
        'limit': limit,
        'offset': offset,
    }
    filters = {
        'date': date_param,
    }

    # Bounding box filter (check if start, end, or route intersects bbox)
    if bbox:
        try:
            min_lat, min_lng, max_lat, max_lng = map(float, bbox.split(','))
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid bbox format: expected min_lat,min_lng,max_lat,max_lng', 'status': 400}), 400
        where.append("""(
            ST_Intersects(m.start_location, ST_MakeEnvelope(%(min_lng)s, %(min_lat)s, %(max_lng)s, %(max_lat)s, 4326))
            OR ST_Intersects(m.end_location, ST_MakeEnvelope(%(min_lng)s, %(min_lat)s, %(max_lng)s, %(max_lat)s, 4326))
            OR ST_Intersects(m.route_geometry, ST_MakeEnvelope(%(min_lng)s, %(min_lat)s, %(max_lng)s, %(max_lat)s, 4326))
        )""")
        params.update({
            'min_lat': min_lat,
            'min_lng': min_lng,
            'max_lat': max_lat,
            'max_lng': max_lng,
        })
        filters['bbox'] = bbox

    if activity_type:
        where.append("m.activity_type = %(activity_type)s")
        params['activity_type'] = activity_type
        filters['activity_type'] = activity_type

    if movement_type:
        valid_types = ['activity', 'breadcrumb_trail', 'gps_track', 'inferred']
        if movement_type not in valid_types:
            return jsonify({'error': f'movement_type must be one of: {", ".join(valid_types)}', 'status': 400}), 400
        where.append("m.movement_type = %(movement_type)s")
        params['movement_type'] = movement_type
        filters['movement_type'] = movement_type

    if source:
        where.append("m.source = %(source)s")
        params['source'] = source
        filters['source'] = source
    else:
        # Arc tracks are denser than Google's; when both sources cover the
        # requested day, show only Arc (pass source= explicitly to override)
        where.append("""(
            m.source != 'google_timeline'
            OR NOT EXISTS (
                SELECT 1
                FROM Movements arc
                WHERE arc.source = 'arc'
                  AND arc.local_start_date <= %(date)s
                  AND arc.local_end_date >= %(date)s
            )
        )""")

    # Movements whose preceding or following visit belongs to this trip
    if trip_id:
        where.append("""(
            m.preceding_visit_id IN (SELECT visit_id FROM Trip_Visits WHERE trip_id = %(trip_id)s)
            OR m.following_visit_id IN (SELECT visit_id FROM Trip_Visits WHERE trip_id = %(trip_id)s)
        )""")
        params['trip_id'] = trip_id
        filters['trip_id'] = trip_id

    if min_distance is not None:
        where.append("m.distance_meters >= %(min_distance)s")
        params['min_distance'] = min_distance
        filters['min_distance'] = min_distance

    if max_distance is not None:
        where.append("m.distance_meters <= %(max_distance)s")
        params['max_distance'] = max_distance
        filters['max_distance'] = max_distance

    route_column = ", ST_AsGeoJSON(m.route_geometry)::jsonb AS route_geojson" if include_routes else ""

    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                {MOVEMENT_COLUMNS},
                m.route_geometry IS NOT NULL AS has_route
                {route_column}
            FROM Movements m
            WHERE {" AND ".join(where)}
            ORDER BY m.start_time DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """, params)
        movements = cursor.fetchall()

    movements_data = []
    for row in movements:
        movement = movement_times(row)
        movement.update({
            'id': row['id'],
            'start_latitude': row['start_latitude'],
            'start_longitude': row['start_longitude'],
            'end_latitude': row['end_latitude'],
            'end_longitude': row['end_longitude'],
            'activity_type': row['activity_type'],
            'movement_type': row['movement_type'],
            'source': row['source'],
            'distance_meters': row['distance_meters'],
            'confidence': row['confidence'],
            'has_route': row['has_route'],
            'preceding_visit_id': row['preceding_visit_id'],
            'following_visit_id': row['following_visit_id'],
        })
        if include_routes:
            movement['route_geojson'] = row['route_geojson']
        movements_data.append(movement)

    return jsonify({
        'movements': movements_data,
        'count': len(movements_data),
        'filters_applied': filters,
    })


@bp.route('/movements/<int:movement_id>')
def get_movement_detail(movement_id):
    """
    Get detailed movement information with full route geometry

    Args:
        movement_id: Movement ID

    Returns:
        JSON response with movement details including route as GeoJSON LineString
    """
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                {MOVEMENT_COLUMNS},
                ST_AsGeoJSON(m.route_geometry)::jsonb AS route_geojson,
                m.source_metadata,
                m.imported_at
            FROM Movements m
            WHERE m.id = %(movement_id)s
        """, {
            'movement_id': movement_id,
        })
        row = cursor.fetchone()

    if not row:
        return jsonify({'error': f'Movement {movement_id} not found', 'status': 404}), 404

    # Calculate speed if possible
    speed_kmh = None
    if row['distance_meters'] and row['duration_minutes'] and row['duration_minutes'] > 0:
        speed_kmh = round((row['distance_meters'] / row['duration_minutes']) * 60 / 1000, 1)

    movement = movement_times(row)
    movement.update({
        'id': row['id'],
        'start_latitude': row['start_latitude'],
        'start_longitude': row['start_longitude'],
        'end_latitude': row['end_latitude'],
        'end_longitude': row['end_longitude'],
        'activity_type': row['activity_type'],
        'movement_type': row['movement_type'],
        'source': row['source'],
        'distance_meters': row['distance_meters'],
        'distance_km': round(row['distance_meters'] / 1000, 2) if row['distance_meters'] else None,
        'confidence': row['confidence'],
        'speed_kmh': speed_kmh,
        'route_geojson': row['route_geojson'],
        'source_metadata': row['source_metadata'],
        'preceding_visit_id': row['preceding_visit_id'],
        'following_visit_id': row['following_visit_id'],
        'imported_at': row['imported_at'].isoformat() if row['imported_at'] else None,
    })

    return jsonify(movement)
