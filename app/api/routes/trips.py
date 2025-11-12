"""
Trips API endpoints
GET /api/trips - List trips with counts
GET /api/trips/<id> - Get trip details with full visit list
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from datetime import datetime
from api.models import Trip, Visit, Photo
from api.database import db

bp = Blueprint('trips', __name__)


@bp.route('/trips')
def get_trips():
    """
    Get trips with visit and photo counts
    
    Query parameters:
        - category: Filter by trip category ('day', 'short', 'long')
        - start_date: ISO datetime (trips overlapping after this date)
        - end_date: ISO datetime (trips overlapping before this date)
        - limit: Maximum results (default 100)
        - offset: Pagination offset (default 0)
    
    Returns:
        JSON response with trips array, each including visit/photo counts
    """
    try:
        # Parse query parameters
        category = request.args.get('category')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = request.args.get('limit', type=int, default=100)
        offset = request.args.get('offset', type=int, default=0)
        
        # Validate limit
        if limit < 1 or limit > 1000:
            return jsonify({'error': 'limit must be between 1 and 1000', 'status': 400}), 400
        
        # Validate category if provided
        valid_categories = ['day', 'short', 'long']
        if category and category not in valid_categories:
            return jsonify({'error': f'category must be one of: {", ".join(valid_categories)}', 'status': 400}), 400
        
        # Build query
        query = db.select(Trip).options(
            db.joinedload(Trip.primary_location)
        )
        
        filters = {}
        
        # Category filter
        if category:
            query = query.where(Trip.trip_category == category)
            filters['category'] = category
        
        # Date range filters (overlap logic)
        # A trip overlaps the date range if:
        # - Trip starts before range ends AND
        # - Trip ends after range starts
        if start_date and end_date:
            # Parse ISO datetime strings
            try:
                start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid date format. Use ISO 8601 format (e.g., 2024-03-01T00:00:00Z)', 'status': 400}), 400
            
            query = query.where(
                Trip.start_time < end_dt,
                Trip.end_time > start_dt
            )
            filters['start_date'] = start_date
            filters['end_date'] = end_date
        elif start_date:
            # Any trip that ends after start_date
            try:
                start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid date format. Use ISO 8601 format (e.g., 2024-03-01T00:00:00Z)', 'status': 400}), 400
            
            query = query.where(Trip.end_time > start_dt)
            filters['start_date'] = start_date
        elif end_date:
            # Any trip that starts before end_date
            try:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid date format. Use ISO 8601 format (e.g., 2024-03-01T00:00:00Z)', 'status': 400}), 400
            
            query = query.where(Trip.start_time < end_dt)
            filters['end_date'] = end_date
        
        # Order by start time descending (most recent first)
        query = query.order_by(Trip.start_time.desc())
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        # Execute query
        trips = db.session.execute(query).scalars().all()
        
        # Get visit and photo counts for each trip
        trip_ids = [trip.id for trip in trips]
        
        visit_counts = {}
        photo_counts = {}
        
        if trip_ids:
            # Count visits per trip
            from api.models import trip_visits
            visit_count_query = db.select(
                trip_visits.c.trip_id,
                func.count(trip_visits.c.visit_id).label('count')
            ).where(
                trip_visits.c.trip_id.in_(trip_ids)
            ).group_by(trip_visits.c.trip_id)
            
            visit_results = db.session.execute(visit_count_query).all()
            visit_counts = {row[0]: row[1] for row in visit_results}
            
            # Count photos per trip
            from api.models import trip_photos
            photo_count_query = db.select(
                trip_photos.c.trip_id,
                func.count(trip_photos.c.photo_id).label('count')
            ).where(
                trip_photos.c.trip_id.in_(trip_ids)
            ).group_by(trip_photos.c.trip_id)
            
            photo_results = db.session.execute(photo_count_query).all()
            photo_counts = {row[0]: row[1] for row in photo_results}
        
        # Format response
        trips_data = []
        for trip in trips:
            # Use display_name if available, fallback to cities array
            display_name = trip.display_name
            if not display_name and trip.cities:
                display_name = ", ".join(trip.cities)
            elif not display_name and trip.primary_location:
                display_name = trip.primary_location.format_name()
            
            trips_data.append({
                'id': trip.id,
                'start_time': trip.start_time.isoformat() if trip.start_time else None,
                'end_time': trip.end_time.isoformat() if trip.end_time else None,
                'category': trip.trip_category,
                'display_name': display_name,
                'visit_count': visit_counts.get(trip.id, 0),
                'photo_count': photo_counts.get(trip.id, 0)
            })
        
        return jsonify({
            'trips': trips_data,
            'count': len(trips_data),
            'filters_applied': filters
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_trips: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500


@bp.route('/trips/<int:trip_id>')
def get_trip_detail(trip_id):
    """
    Get detailed trip information with full visit list
    
    Args:
        trip_id: Trip ID
    
    Returns:
        JSON response with trip details and complete visit list
    """
    try:
        # Get trip with relationships
        query = db.select(Trip).options(
            db.joinedload(Trip.primary_location),
            db.joinedload(Trip.visits).joinedload(Visit.location_rel)
        ).where(Trip.id == trip_id)
        
        # Use unique() for joined eager loads against collections
        trip = db.session.execute(query).unique().scalar_one_or_none()
        
        if not trip:
            return jsonify({'error': f'Trip {trip_id} not found', 'status': 404}), 404
        
        # Get photo count (photos are fetched separately via /api/photos?trip_id=X)
        from api.models import trip_photos
        photo_count_query = db.select(
            func.count(trip_photos.c.photo_id)
        ).where(trip_photos.c.trip_id == trip_id)
        
        photo_count = db.session.execute(photo_count_query).scalar() or 0
        
        # Use display_name if available, fallback to cities array
        display_name = trip.display_name
        if not display_name and trip.cities:
            display_name = ", ".join(trip.cities)
        elif not display_name and trip.primary_location:
            display_name = trip.primary_location.format_name()
        
        # Format visits
        visits_data = []
        for visit in trip.visits:
            # Get lat/lng from geography point
            if visit.location:
                coords = db.session.execute(
                    db.select(
                        func.ST_Y(func.ST_GeomFromWKB(visit.location)).label('lat'),
                        func.ST_X(func.ST_GeomFromWKB(visit.location)).label('lng')
                    )
                ).first()
                lat, lng = coords.lat, coords.lng
            else:
                lat, lng = None, None
            
            # Get photo count for this visit
            visit_photo_count = db.session.execute(
                db.select(func.count(Photo.id)).where(Photo.visit_id == visit.id)
            ).scalar() or 0
            
            visits_data.append({
                'id': visit.id,
                'start_time': visit.start_time.isoformat() if visit.start_time else None,
                'end_time': visit.end_time.isoformat() if visit.end_time else None,
                'duration_minutes': visit.duration_minutes,
                'latitude': lat,
                'longitude': lng,
                'location_name': visit.location_name,
                'semantic_type': visit.semantic_type,
                'photo_count': visit_photo_count
            })
        
        return jsonify({
            'id': trip.id,
            'start_time': trip.start_time.isoformat() if trip.start_time else None,
            'end_time': trip.end_time.isoformat() if trip.end_time else None,
            'category': trip.trip_category,
            'display_name': display_name,
            'visit_count': len(visits_data),
            'photo_count': photo_count,
            'visits': visits_data
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_trip_detail: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
