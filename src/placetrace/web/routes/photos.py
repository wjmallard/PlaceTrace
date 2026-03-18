"""
Photos API endpoint
GET /api/photos - List photos with filtering
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from placetrace.web.models import Photo, trip_photos
from placetrace.web.database import db

bp = Blueprint('photos', __name__)


@bp.route('/photos')
def get_photos():
    """
    Get photos with optional filters
    
    Query parameters:
        - visit_id: Filter by visit ID
        - trip_id: Filter by trip ID
        - location_id: Filter by location ID
        - start_date: ISO datetime (inclusive, uses capture_datetime)
        - end_date: ISO datetime (inclusive, uses capture_datetime)
        - limit: Maximum results (default 100)
        - offset: Pagination offset (default 0)
    
    Returns:
        JSON response with photos array and count
    """
    try:
        # Parse query parameters
        visit_id = request.args.get('visit_id', type=int)
        trip_id = request.args.get('trip_id', type=int)
        location_id = request.args.get('location_id', type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = request.args.get('limit', type=int, default=100)
        offset = request.args.get('offset', type=int, default=0)
        
        # Validate limit
        if limit < 1 or limit > 1000:
            return jsonify({'error': 'limit must be between 1 and 1000', 'status': 400}), 400
        
        # Build query
        query = db.select(Photo).options(
            db.joinedload(Photo.location_rel)
        )
        
        filters = {}
        
        # Visit filter
        if visit_id:
            query = query.where(Photo.visit_id == visit_id)
            filters['visit_id'] = visit_id
        
        # Trip filter
        if trip_id:
            query = query.join(trip_photos).where(trip_photos.c.trip_id == trip_id)
            filters['trip_id'] = trip_id
        
        # Location filter
        if location_id:
            query = query.where(Photo.location_id == location_id)
            filters['location_id'] = location_id
        
        # Date range filters (on capture_datetime)
        if start_date:
            query = query.where(Photo.capture_datetime >= start_date)
            filters['start_date'] = start_date
        
        if end_date:
            query = query.where(Photo.capture_datetime <= end_date)
            filters['end_date'] = end_date
        
        # Order by capture time descending (most recent first)
        query = query.order_by(Photo.capture_datetime.desc())
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        # Execute query
        photos = db.session.execute(query).scalars().all()
        
        # Format response
        photos_data = []
        for photo in photos:
            photos_data.append({
                'id': photo.id,
                'file_path': photo.file_path,
                'capture_datetime': photo.capture_datetime.isoformat() if photo.capture_datetime else None,
                'width': photo.width,
                'height': photo.height,
                'latitude': photo.latitude,
                'longitude': photo.longitude,
                'location_name': photo.location_name,
                'visit_id': photo.visit_id,
                'exif': photo.exif
            })
        
        return jsonify({
            'photos': photos_data,
            'count': len(photos_data),
            'filters_applied': filters
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_photos: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
