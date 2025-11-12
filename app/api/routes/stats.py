"""
Statistics API endpoints
GET /api/stats/overview - Overall statistics across all data
GET /api/stats/movement - Movement aggregate statistics by activity type
"""

from flask import Blueprint, jsonify, current_app
from sqlalchemy import func
from api.models import Movement, Visit, Photo, Trip, Location
from api.database import db

bp = Blueprint('stats', __name__)


@bp.route('/stats/overview')
def get_overview():
    """
    Get overall statistics across all data
    
    Returns:
        JSON response with counts of visits, photos, trips, movements, and locations
    """
    try:
        # Count totals
        visit_count = db.session.execute(db.select(func.count(Visit.id))).scalar() or 0
        photo_count = db.session.execute(db.select(func.count(Photo.id))).scalar() or 0
        trip_count = db.session.execute(db.select(func.count(Trip.id))).scalar() or 0
        movement_count = db.session.execute(db.select(func.count(Movement.id))).scalar() or 0
        location_count = db.session.execute(db.select(func.count(Location.id))).scalar() or 0
        
        # Date ranges
        visit_date_range = db.session.execute(
            db.select(
                func.min(Visit.start_time).label('earliest'),
                func.max(Visit.end_time).label('latest')
            )
        ).first()
        
        photo_date_range = db.session.execute(
            db.select(
                func.min(Photo.capture_datetime).label('earliest'),
                func.max(Photo.capture_datetime).label('latest')
            )
        ).first()
        
        # Movement totals
        movement_totals = db.session.execute(
            db.select(
                func.sum(Movement.distance_meters).label('total_distance_m'),
                func.sum(Movement.duration_minutes).label('total_duration_min')
            )
        ).first()
        
        return jsonify({
            'counts': {
                'visits': visit_count,
                'photos': photo_count,
                'trips': trip_count,
                'movements': movement_count,
                'locations': location_count
            },
            'date_ranges': {
                'visits': {
                    'earliest': visit_date_range.earliest.isoformat() if visit_date_range.earliest else None,
                    'latest': visit_date_range.latest.isoformat() if visit_date_range.latest else None
                },
                'photos': {
                    'earliest': photo_date_range.earliest.isoformat() if photo_date_range.earliest else None,
                    'latest': photo_date_range.latest.isoformat() if photo_date_range.latest else None
                }
            },
            'movement_totals': {
                'total_distance_km': round(movement_totals.total_distance_m / 1000, 1) if movement_totals.total_distance_m else 0,
                'total_duration_hours': round(movement_totals.total_duration_min / 60, 1) if movement_totals.total_duration_min else 0
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_overview: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500


@bp.route('/stats/movement')
def get_movement_stats():
    """
    Get aggregate movement statistics broken down by activity type
    
    Returns:
        JSON response with:
        - Total distance and duration across all movements
        - Breakdown by activity type (walking, cycling, driving, etc.)
        - Breakdown by movement type (activity vs breadcrumb_trail)
        - Breakdown by source (google_timeline, strava, etc.)
    """
    try:
        # Overall totals
        overall = db.session.execute(
            db.select(
                func.count(Movement.id).label('total_count'),
                func.sum(Movement.distance_meters).label('total_distance_m'),
                func.sum(Movement.duration_minutes).label('total_duration_min')
            )
        ).first()
        
        # By activity type (for activities only, not breadcrumb trails)
        by_activity_type = db.session.execute(
            db.select(
                Movement.activity_type,
                func.count(Movement.id).label('count'),
                func.sum(Movement.distance_meters).label('total_distance_m'),
                func.sum(Movement.duration_minutes).label('total_duration_min'),
                func.avg(Movement.confidence).label('avg_confidence')
            ).where(
                Movement.movement_type == 'activity',
                Movement.activity_type.isnot(None)
            ).group_by(Movement.activity_type)
            .order_by(func.sum(Movement.distance_meters).desc())
        ).all()
        
        # By movement type
        by_movement_type = db.session.execute(
            db.select(
                Movement.movement_type,
                func.count(Movement.id).label('count'),
                func.sum(Movement.distance_meters).label('total_distance_m'),
                func.sum(Movement.duration_minutes).label('total_duration_min')
            ).group_by(Movement.movement_type)
            .order_by(func.count(Movement.id).desc())
        ).all()
        
        # By source
        by_source = db.session.execute(
            db.select(
                Movement.source,
                func.count(Movement.id).label('count'),
                func.sum(Movement.distance_meters).label('total_distance_m'),
                func.sum(Movement.duration_minutes).label('total_duration_min')
            ).group_by(Movement.source)
            .order_by(func.count(Movement.id).desc())
        ).all()
        
        # Movements with route geometry
        with_route_count = db.session.execute(
            db.select(func.count(Movement.id))
            .where(Movement.route_geometry.isnot(None))
        ).scalar() or 0
        
        # Format response
        return jsonify({
            'overall': {
                'total_movements': overall.total_count or 0,
                'total_distance_km': round(overall.total_distance_m / 1000, 1) if overall.total_distance_m else 0,
                'total_duration_hours': round(overall.total_duration_min / 60, 1) if overall.total_duration_min else 0,
                'movements_with_route': with_route_count,
                'route_coverage_pct': round(100 * with_route_count / overall.total_count, 1) if overall.total_count else 0
            },
            'by_activity_type': [
                {
                    'activity_type': row.activity_type,
                    'count': row.count,
                    'distance_km': round(row.total_distance_m / 1000, 1) if row.total_distance_m else 0,
                    'duration_hours': round(row.total_duration_min / 60, 1) if row.total_duration_min else 0,
                    'avg_confidence': round(row.avg_confidence, 3) if row.avg_confidence else None
                }
                for row in by_activity_type
            ],
            'by_movement_type': [
                {
                    'movement_type': row.movement_type,
                    'count': row.count,
                    'distance_km': round(row.total_distance_m / 1000, 1) if row.total_distance_m else 0,
                    'duration_hours': round(row.total_duration_min / 60, 1) if row.total_duration_min else 0
                }
                for row in by_movement_type
            ],
            'by_source': [
                {
                    'source': row.source,
                    'count': row.count,
                    'distance_km': round(row.total_distance_m / 1000, 1) if row.total_distance_m else 0,
                    'duration_hours': round(row.total_duration_min / 60, 1) if row.total_duration_min else 0
                }
                for row in by_source
            ]
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_movement_stats: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
