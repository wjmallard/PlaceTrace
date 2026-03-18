"""
Locations API endpoint
GET /api/locations - List locations with filtering
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from placetrace.web.models import Location, Visit, Photo
from placetrace.web.database import db

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
        JSON response with locations array, each including visit/photo counts
    """
    try:
        # Parse query parameters
        country = request.args.get('country')
        state = request.args.get('state')
        city = request.args.get('city')
        admin_level = request.args.get('admin_level', type=int)
        limit = request.args.get('limit', type=int, default=100)
        offset = request.args.get('offset', type=int, default=0)
        
        # Validate limit
        if limit < 1 or limit > 1000:
            return jsonify({'error': 'limit must be between 1 and 1000', 'status': 400}), 400
        
        # Validate admin_level if provided
        if admin_level and admin_level not in [2, 4, 6, 8]:
            return jsonify({'error': 'admin_level must be 2, 4, 6, or 8', 'status': 400}), 400
        
        # Build query
        query = db.select(Location)
        
        filters = {}
        
        # Apply filters
        if country:
            query = query.where(Location.country == country)
            filters['country'] = country
        
        if state:
            query = query.where(Location.state == state)
            filters['state'] = state
        
        if city:
            query = query.where(Location.city == city)
            filters['city'] = city
        
        if admin_level:
            query = query.where(Location.admin_level == admin_level)
            filters['admin_level'] = admin_level
        
        # Order by admin level (most specific first), then alphabetically
        query = query.order_by(Location.admin_level.desc(), Location.city, Location.state, Location.country)
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        # Execute query
        locations = db.session.execute(query).scalars().all()
        
        # Get visit and photo counts for each location
        location_ids = [loc.id for loc in locations]
        
        visit_counts = {}
        photo_counts = {}
        
        if location_ids:
            # Count visits per location
            visit_count_query = db.select(
                Visit.location_id,
                func.count(Visit.id).label('count')
            ).where(
                Visit.location_id.in_(location_ids)
            ).group_by(Visit.location_id)
            
            visit_results = db.session.execute(visit_count_query).all()
            visit_counts = {row[0]: row[1] for row in visit_results}
            
            # Count photos per location
            photo_count_query = db.select(
                Photo.location_id,
                func.count(Photo.id).label('count')
            ).where(
                Photo.location_id.in_(location_ids)
            ).group_by(Photo.location_id)
            
            photo_results = db.session.execute(photo_count_query).all()
            photo_counts = {row[0]: row[1] for row in photo_results}
        
        # Format response
        locations_data = []
        for location in locations:
            # Get centroid coordinates if available
            lat, lng = None, None
            if location.centroid:
                coords = db.session.execute(
                    db.select(
                        func.ST_Y(func.ST_GeomFromWKB(location.centroid)).label('lat'),
                        func.ST_X(func.ST_GeomFromWKB(location.centroid)).label('lng')
                    )
                ).first()
                lat, lng = coords.lat, coords.lng
            
            locations_data.append({
                'id': location.id,
                'city': location.city,
                'county': location.county,
                'state': location.state,
                'country': location.country,
                'admin_level': location.admin_level,
                'formatted_name': location.format_name(),
                'centroid_latitude': lat,
                'centroid_longitude': lng,
                'visit_count': visit_counts.get(location.id, 0),
                'photo_count': photo_counts.get(location.id, 0)
            })
        
        return jsonify({
            'locations': locations_data,
            'count': len(locations_data),
            'filters_applied': filters
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_locations: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
