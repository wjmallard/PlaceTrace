"""
Trips API endpoints
GET /api/trips - List trips with counts
GET /api/trips/<id> - Get trip details with full visit list
"""

from datetime import datetime

from flask import Blueprint, request, jsonify

from location_history.config import TRIP_CATEGORIES
from location_history.web.database import get_db
from location_history.web.serialize import (
    VISIT_COLUMNS,
    format_location_name,
    visit_dict,
)

bp = Blueprint('trips', __name__)

TRIP_COLUMNS = """
    t.id,
    t.start_time,
    t.end_time,
    t.local_start_date,
    t.local_start_time,
    t.local_end_date,
    t.local_end_time,
    t.trip_category,
    t.cities,
    t.display_name,
    l.city,
    l.county,
    l.state,
    l.country
"""

DATE_FORMAT_ERROR = {
    'error': 'Invalid date format. Use YYYY-MM-DD or ISO 8601 format (e.g., 2024-03-01 or 2024-03-01T00:00:00Z)',
    'status': 400,
}


def is_local_date(value):
    """True if the value looks like a plain YYYY-MM-DD date."""
    return len(value) == 10 and value.count('-') == 2


def trip_dict(row, visit_count):
    """Serialize a trip row (with joined primary-location columns) for the API."""
    # Use display_name if available, fall back to the cities array,
    # then to the primary location's name
    display_name = row['display_name']
    if not display_name and row['cities']:
        display_name = ", ".join(row['cities'])
    elif not display_name and row['country']:
        display_name = format_location_name(row)

    return {
        'id': row['id'],
        'start_time': row['start_time'].isoformat() if row['start_time'] else None,
        'end_time': row['end_time'].isoformat() if row['end_time'] else None,
        'local_start_date': row['local_start_date'].isoformat() if row['local_start_date'] else None,
        'local_start_time': row['local_start_time'].isoformat() if row['local_start_time'] else None,
        'local_end_date': row['local_end_date'].isoformat() if row['local_end_date'] else None,
        'local_end_time': row['local_end_time'].isoformat() if row['local_end_time'] else None,
        'category': row['trip_category'],
        'display_name': display_name,
        'visit_count': visit_count,
    }


@bp.route('/trips')
def get_trips():
    """
    Get trips with visit counts

    Query parameters:
        - category: Filter by config-defined category name (e.g. 'Day Trip')
        - start_date: YYYY-MM-DD (local dates) or ISO datetime (UTC); trips overlapping after this date
        - end_date: YYYY-MM-DD (local dates) or ISO datetime (UTC); trips overlapping before this date
        - limit: Maximum results (default 100)
        - offset: Pagination offset (default 0)

    Returns:
        JSON response with trips array, each including visit counts
    """
    category = request.args.get('category')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    limit = request.args.get('limit', type=int, default=100)
    offset = request.args.get('offset', type=int, default=0)

    if limit < 1 or limit > 1000:
        return jsonify({'error': 'limit must be between 1 and 1000', 'status': 400}), 400

    valid_categories = [c['name'] for c in TRIP_CATEGORIES]
    if category and category not in valid_categories:
        return jsonify({'error': f'category must be one of: {", ".join(valid_categories)}', 'status': 400}), 400

    where = []
    params = {
        'limit': limit,
        'offset': offset,
    }
    filters = {}

    if category:
        where.append("t.trip_category = %(category)s")
        params['category'] = category
        filters['category'] = category

    # Date range filters use overlap logic: a trip overlaps the range if it
    # starts before the range ends AND ends after the range starts.
    # The start_date's format decides whether local dates or UTC times are compared.
    try:
        if start_date and end_date:
            if is_local_date(start_date):
                params['range_start'] = datetime.strptime(start_date, '%Y-%m-%d').date()
                params['range_end'] = datetime.strptime(end_date, '%Y-%m-%d').date()
                where.append("t.local_start_date <= %(range_end)s")
                where.append("t.local_end_date >= %(range_start)s")
            else:
                params['range_start'] = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                params['range_end'] = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                where.append("t.start_time < %(range_end)s")
                where.append("t.end_time > %(range_start)s")
            filters['start_date'] = start_date
            filters['end_date'] = end_date
        elif start_date:
            # Any trip that ends after start_date
            if is_local_date(start_date):
                params['range_start'] = datetime.strptime(start_date, '%Y-%m-%d').date()
                where.append("t.local_end_date >= %(range_start)s")
            else:
                params['range_start'] = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                where.append("t.end_time > %(range_start)s")
            filters['start_date'] = start_date
        elif end_date:
            # Any trip that starts before end_date
            if is_local_date(end_date):
                params['range_end'] = datetime.strptime(end_date, '%Y-%m-%d').date()
                where.append("t.local_start_date <= %(range_end)s")
            else:
                params['range_end'] = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                where.append("t.start_time < %(range_end)s")
            filters['end_date'] = end_date
    except ValueError:
        return jsonify(DATE_FORMAT_ERROR), 400

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                {TRIP_COLUMNS},
                count(tv.visit_id) AS visit_count
            FROM Trips t
            LEFT JOIN Locations l ON l.id = t.primary_location_id
            LEFT JOIN Trip_Visits tv ON tv.trip_id = t.id
            {where_sql}
            GROUP BY t.id, l.id
            ORDER BY t.start_time DESC, t.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """, params)
        trips = cursor.fetchall()

    return jsonify({
        'trips': [trip_dict(row, row['visit_count']) for row in trips],
        'count': len(trips),
        'filters_applied': filters,
    })


@bp.route('/trips/<int:trip_id>')
def get_trip_detail(trip_id):
    """
    Get detailed trip information with full visit list

    Args:
        trip_id: Trip ID

    Returns:
        JSON response with trip details and complete visit list
    """
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                {TRIP_COLUMNS}
            FROM Trips t
            LEFT JOIN Locations l ON l.id = t.primary_location_id
            WHERE t.id = %(trip_id)s
        """, {
            'trip_id': trip_id,
        })
        trip = cursor.fetchone()

        if not trip:
            return jsonify({'error': f'Trip {trip_id} not found', 'status': 404}), 404

        cursor.execute(f"""
            SELECT
                {VISIT_COLUMNS}
            FROM Trip_Visits tv
            JOIN Visits v ON v.id = tv.visit_id
            LEFT JOIN Locations l ON l.id = v.location_id
            WHERE tv.trip_id = %(trip_id)s
            ORDER BY v.start_time
        """, {
            'trip_id': trip_id,
        })
        visits = cursor.fetchall()

    response = trip_dict(trip, len(visits))
    response['visits'] = [visit_dict(row, include_local=False) for row in visits]

    return jsonify(response)
