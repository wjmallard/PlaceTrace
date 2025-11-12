"""
Visits API endpoint
GET /api/visits - List visits with filtering
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func, and_
from datetime import datetime
from api.models import Visit, Photo
from api.database import db

bp = Blueprint('visits', __name__)


@bp.route('/visits')
def get_visits():
    """
    Get visits with optional filters
    
    Query parameters:
        - bbox: Bounding box as 'min_lat,min_lng,max_lat,max_lng'
        - start_date: ISO datetime (inclusive)
        - end_date: ISO datetime (inclusive)
        - location_id: Filter by location ID
        - trip_id: Filter by trip ID
        - limit: Maximum results (default 1000)
        - offset: Pagination offset (default 0)
    
    Returns:
        JSON response with visits array, count, and filters_applied
    """
    try:
        # Parse query parameters
        bbox = request.args.get('bbox')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        location_id = request.args.get('location_id', type=int)
        trip_id = request.args.get('trip_id', type=int)
        limit = request.args.get('limit', type=int, default=1000)
        offset = request.args.get('offset', type=int, default=0)
        
        # Validate limit
        if limit < 1 or limit > 10000:
            return jsonify({'error': 'limit must be between 1 and 10000', 'status': 400}), 400
        
        # Build query
        query = db.select(Visit).options(
            db.joinedload(Visit.location_rel)
        )
        
        filters = {}
        
        # Bounding box filter
        if bbox:
            try:
                min_lat, min_lng, max_lat, max_lng = map(float, bbox.split(','))
                bbox_geom = func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326)
                query = query.where(func.ST_Intersects(Visit.location, bbox_geom))
                filters['bbox'] = bbox
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid bbox format: expected min_lat,min_lng,max_lat,max_lng', 'status': 400}), 400
        
        # Date range filters
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                query = query.where(Visit.start_time >= start_dt)
                filters['start_date'] = start_date
            except (ValueError, AttributeError) as e:
                return jsonify({'error': f'Invalid start_date format: {str(e)}', 'status': 400}), 400
        
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                query = query.where(Visit.end_time <= end_dt)
                filters['end_date'] = end_date
            except (ValueError, AttributeError) as e:
                return jsonify({'error': f'Invalid end_date format: {str(e)}', 'status': 400}), 400
        
        # Location filter
        if location_id:
            query = query.where(Visit.location_id == location_id)
            filters['location_id'] = location_id
        
        # Trip filter
        if trip_id:
            from api.models import trip_visits
            query = query.join(trip_visits).where(trip_visits.c.trip_id == trip_id)
            filters['trip_id'] = trip_id
        
        # Order by start time descending (most recent first)
        query = query.order_by(Visit.start_time.desc())
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        # Execute query
        visits = db.session.execute(query).scalars().all()
        
        # Get photo counts for each visit (subquery approach for efficiency)
        visit_ids = [v.id for v in visits]
        photo_counts = {}
        if visit_ids:
            count_query = db.select(
                Photo.visit_id,
                func.count(Photo.id).label('count')
            ).where(
                Photo.visit_id.in_(visit_ids)
            ).group_by(Photo.visit_id)
            
            counts_result = db.session.execute(count_query).all()
            photo_counts = {row[0]: row[1] for row in counts_result}
        
        # Format response
        visits_data = []
        for visit in visits:
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
            
            visits_data.append({
                'id': visit.id,
                'start_time': visit.start_time.isoformat() if visit.start_time else None,
                'end_time': visit.end_time.isoformat() if visit.end_time else None,
                'duration_minutes': visit.duration_minutes,
                'latitude': lat,
                'longitude': lng,
                'location_name': visit.location_name,
                'semantic_type': visit.semantic_type,
                'photo_count': photo_counts.get(visit.id, 0)
            })
        
        return jsonify({
            'visits': visits_data,
            'count': len(visits_data),
            'filters_applied': filters
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_visits: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
