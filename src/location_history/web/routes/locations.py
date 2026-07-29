"""
Locations API endpoint
GET /api/locations - List locations with filtering
"""

from flask import Blueprint, request, jsonify

from location_history.web.database import get_db
from location_history.web.serialize import format_location_name

bp = Blueprint('locations', __name__)


@bp.route('/locations')
def get_locations():
    """
    Get locations with optional filters

    Query parameters:
        - country: Filter by country name
        - state: Filter by state name
        - city: Filter by city name
        - admin_level: Filter by admin level (2, 4, 6, 8)
        - limit: Maximum results (default 100)
        - offset: Pagination offset (default 0)

    Returns:
        JSON response with locations array, each including visit counts
    """
    country = request.args.get('country')
    state = request.args.get('state')
    city = request.args.get('city')
    admin_level = request.args.get('admin_level', type=int)
    limit = request.args.get('limit', type=int, default=100)
    offset = request.args.get('offset', type=int, default=0)

    if limit < 1 or limit > 1000:
        return jsonify({'error': 'limit must be between 1 and 1000', 'status': 400}), 400

    if admin_level and admin_level not in [2, 4, 6, 8]:
        return jsonify({'error': 'admin_level must be 2, 4, 6, or 8', 'status': 400}), 400

    where = []
    params = {
        'limit': limit,
        'offset': offset,
    }
    filters = {}

    if country:
        where.append("l.country = %(country)s")
        params['country'] = country
        filters['country'] = country

    if state:
        where.append("l.state = %(state)s")
        params['state'] = state
        filters['state'] = state

    if city:
        where.append("l.city = %(city)s")
        params['city'] = city
        filters['city'] = city

    if admin_level:
        where.append("l.admin_level = %(admin_level)s")
        params['admin_level'] = admin_level
        filters['admin_level'] = admin_level

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                l.id,
                l.city,
                l.county,
                l.state,
                l.country,
                l.admin_level,
                ST_Y(l.centroid::geometry) AS centroid_latitude,
                ST_X(l.centroid::geometry) AS centroid_longitude,
                count(v.id) AS visit_count
            FROM Locations l
            LEFT JOIN Visits v ON v.location_id = l.id
            {where_sql}
            GROUP BY l.id
            ORDER BY l.admin_level DESC, l.city, l.state, l.country, l.id
            LIMIT %(limit)s OFFSET %(offset)s
        """, params)
        locations = cursor.fetchall()

    return jsonify({
        'locations': [
            {
                'id': row['id'],
                'city': row['city'],
                'county': row['county'],
                'state': row['state'],
                'country': row['country'],
                'admin_level': row['admin_level'],
                'formatted_name': format_location_name(row),
                'centroid_latitude': row['centroid_latitude'],
                'centroid_longitude': row['centroid_longitude'],
                'visit_count': row['visit_count'],
            }
            for row in locations
        ],
        'count': len(locations),
        'filters_applied': filters,
    })
