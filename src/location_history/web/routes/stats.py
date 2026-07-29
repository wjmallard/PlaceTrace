"""
Statistics API endpoints
GET /api/stats/overview - Overall statistics across all data
GET /api/stats/movement - Movement aggregate statistics by activity type
"""

from flask import Blueprint, jsonify

from location_history.web.database import get_db

bp = Blueprint('stats', __name__)


@bp.route('/stats/overview')
def get_overview():
    """Overall statistics: entity counts, visit date range, movement totals."""
    conn = get_db()

    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT
                (SELECT count(*) FROM Visits) AS visits,
                (SELECT count(*) FROM Trips) AS trips,
                (SELECT count(*) FROM Movements) AS movements,
                (SELECT count(*) FROM Locations) AS locations
        """)
        counts = cursor.fetchone()

        cursor.execute("""
            SELECT
                min(start_time) AS earliest,
                max(end_time) AS latest
            FROM Visits
        """)
        visit_range = cursor.fetchone()

        cursor.execute("""
            SELECT
                sum(distance_meters) AS total_distance_m,
                sum(duration_minutes) AS total_duration_min
            FROM Movements
        """)
        totals = cursor.fetchone()

    return jsonify({
        'counts': {
            'visits': counts['visits'],
            'trips': counts['trips'],
            'movements': counts['movements'],
            'locations': counts['locations'],
        },
        'date_ranges': {
            'visits': {
                'earliest': visit_range['earliest'].isoformat() if visit_range['earliest'] else None,
                'latest': visit_range['latest'].isoformat() if visit_range['latest'] else None,
            },
        },
        'movement_totals': {
            'total_distance_km': round(totals['total_distance_m'] / 1000, 1) if totals['total_distance_m'] else 0,
            'total_duration_hours': round(totals['total_duration_min'] / 60, 1) if totals['total_duration_min'] else 0,
        },
    })


@bp.route('/stats/movement')
def get_movement_stats():
    """Aggregate movement statistics by activity type, movement type, and source."""
    conn = get_db()

    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT
                count(*) AS total_count,
                sum(distance_meters) AS total_distance_m,
                sum(duration_minutes) AS total_duration_min
            FROM Movements
        """)
        overall = cursor.fetchone()

        cursor.execute("""
            SELECT
                activity_type,
                count(*) AS movement_count,
                sum(distance_meters) AS total_distance_m,
                sum(duration_minutes) AS total_duration_min,
                avg(confidence) AS avg_confidence
            FROM Movements
            WHERE movement_type = 'activity'
              AND activity_type IS NOT NULL
            GROUP BY activity_type
            ORDER BY sum(distance_meters) DESC
        """)
        by_activity_type = cursor.fetchall()

        cursor.execute("""
            SELECT
                movement_type,
                count(*) AS movement_count,
                sum(distance_meters) AS total_distance_m,
                sum(duration_minutes) AS total_duration_min
            FROM Movements
            GROUP BY movement_type
            ORDER BY count(*) DESC
        """)
        by_movement_type = cursor.fetchall()

        cursor.execute("""
            SELECT
                source,
                count(*) AS movement_count,
                sum(distance_meters) AS total_distance_m,
                sum(duration_minutes) AS total_duration_min
            FROM Movements
            GROUP BY source
            ORDER BY count(*) DESC
        """)
        by_source = cursor.fetchall()

        cursor.execute("""
            SELECT count(*)
            FROM Movements
            WHERE route_geometry IS NOT NULL
        """)
        with_route_count = cursor.fetchone()['count']

    return jsonify({
        'overall': {
            'total_movements': overall['total_count'] or 0,
            'total_distance_km': round(overall['total_distance_m'] / 1000, 1) if overall['total_distance_m'] else 0,
            'total_duration_hours': round(overall['total_duration_min'] / 60, 1) if overall['total_duration_min'] else 0,
            'movements_with_route': with_route_count,
            'route_coverage_pct': round(100 * with_route_count / overall['total_count'], 1) if overall['total_count'] else 0,
        },
        'by_activity_type': [
            {
                'activity_type': row['activity_type'],
                'count': row['movement_count'],
                'distance_km': round(row['total_distance_m'] / 1000, 1) if row['total_distance_m'] else 0,
                'duration_hours': round(row['total_duration_min'] / 60, 1) if row['total_duration_min'] else 0,
                'avg_confidence': round(row['avg_confidence'], 3) if row['avg_confidence'] else None,
            }
            for row in by_activity_type
        ],
        'by_movement_type': [
            {
                'movement_type': row['movement_type'],
                'count': row['movement_count'],
                'distance_km': round(row['total_distance_m'] / 1000, 1) if row['total_distance_m'] else 0,
                'duration_hours': round(row['total_duration_min'] / 60, 1) if row['total_duration_min'] else 0,
            }
            for row in by_movement_type
        ],
        'by_source': [
            {
                'source': row['source'],
                'count': row['movement_count'],
                'distance_km': round(row['total_distance_m'] / 1000, 1) if row['total_distance_m'] else 0,
                'duration_hours': round(row['total_duration_min'] / 60, 1) if row['total_duration_min'] else 0,
            }
            for row in by_source
        ],
    })
