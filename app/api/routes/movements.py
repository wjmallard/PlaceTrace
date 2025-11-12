"""
Movements API endpoints
GET /api/movements - List movements with filtering
GET /api/movements/<id> - Get movement detail with route geometry
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from datetime import datetime
from api.models import Movement, Visit
from api.database import db

bp = Blueprint('movements', __name__)


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
        - include_routes: Include route geometry as [[lat, lon], ...] arrays (default: false)
        - limit: Maximum results (default 1000)
        - offset: Pagination offset (default 0)
    
    Returns:
        JSON response with movements array, count, date, and filters_applied
    """
    try:
        # Date parameter is required
        date_param = request.args.get('date')
        if not date_param:
            return jsonify({
                'error': 'date parameter is required (format: YYYY-MM-DD)',
                'status': 400
            }), 400
        
        # Parse and validate date
        try:
            from datetime import datetime, timedelta, timezone
            date_obj = datetime.strptime(date_param, '%Y-%m-%d')
            # Set to full day range in UTC
            start_dt = date_obj.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            end_dt = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
        except ValueError:
            return jsonify({
                'error': 'Invalid date format. Use YYYY-MM-DD (e.g., 2024-12-01)',
                'status': 400
            }), 400
        
        # Parse other query parameters
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
        
        # Validate limit
        if limit < 1 or limit > 10000:
            return jsonify({'error': 'limit must be between 1 and 10000', 'status': 400}), 400
        
        # Build query
        query = db.select(Movement)
        
        filters = {}
        
        # Bounding box filter (check if route intersects bbox)
        if bbox:
            try:
                min_lat, min_lng, max_lat, max_lng = map(float, bbox.split(','))
                bbox_geom = func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326)
                
                # Check if start, end, or route intersects bbox
                query = query.where(
                    db.or_(
                        func.ST_Intersects(Movement.start_location, bbox_geom),
                        func.ST_Intersects(Movement.end_location, bbox_geom),
                        func.ST_Intersects(Movement.route_geometry, bbox_geom)
                    )
                )
                filters['bbox'] = bbox
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid bbox format: expected min_lat,min_lng,max_lat,max_lng', 'status': 400}), 400
        
        # Date range filters (from required date parameter)
        query = query.where(Movement.start_time >= start_dt)
        query = query.where(Movement.end_time <= end_dt)
        filters['date'] = date_param
        
        # Activity type filter
        if activity_type:
            query = query.where(Movement.activity_type == activity_type)
            filters['activity_type'] = activity_type
        
        # Movement type filter
        if movement_type:
            valid_types = ['activity', 'breadcrumb_trail', 'gps_track', 'inferred']
            if movement_type not in valid_types:
                return jsonify({'error': f'movement_type must be one of: {", ".join(valid_types)}', 'status': 400}), 400
            query = query.where(Movement.movement_type == movement_type)
            filters['movement_type'] = movement_type
        
        # Source filter
        if source:
            query = query.where(Movement.source == source)
            filters['source'] = source
        
        # Trip filter (movements connecting visits in this trip)
        if trip_id:
            from api.models import trip_visits
            
            # Get visit IDs in this trip
            visit_ids_query = db.select(trip_visits.c.visit_id).where(trip_visits.c.trip_id == trip_id)
            visit_ids = db.session.execute(visit_ids_query).scalars().all()
            
            if visit_ids:
                # Find movements where preceding or following visit is in this trip
                query = query.where(
                    db.or_(
                        Movement.preceding_visit_id.in_(visit_ids),
                        Movement.following_visit_id.in_(visit_ids)
                    )
                )
                filters['trip_id'] = trip_id
        
        # Distance filters
        if min_distance is not None:
            query = query.where(Movement.distance_meters >= min_distance)
            filters['min_distance'] = min_distance
        
        if max_distance is not None:
            query = query.where(Movement.distance_meters <= max_distance)
            filters['max_distance'] = max_distance
        
        # Order by start time descending (most recent first)
        query = query.order_by(Movement.start_time.desc())
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        # Execute query
        movements = db.session.execute(query).scalars().all()
        
        # Format response
        movements_data = []
        for movement in movements:
            # Get start coordinates
            if movement.start_location:
                start_coords = db.session.execute(
                    db.select(
                        func.ST_Y(func.ST_GeomFromWKB(movement.start_location)).label('lat'),
                        func.ST_X(func.ST_GeomFromWKB(movement.start_location)).label('lng')
                    )
                ).first()
                start_lat, start_lng = start_coords.lat, start_coords.lng
            else:
                start_lat, start_lng = None, None
            
            # Get end coordinates
            if movement.end_location:
                end_coords = db.session.execute(
                    db.select(
                        func.ST_Y(func.ST_GeomFromWKB(movement.end_location)).label('lat'),
                        func.ST_X(func.ST_GeomFromWKB(movement.end_location)).label('lng')
                    )
                ).first()
                end_lat, end_lng = end_coords.lat, end_coords.lng
            else:
                end_lat, end_lng = None, None
            
            # Extract route geometry if requested (as GeoJSON)
            route_geojson = None
            if include_routes and movement.route_geometry:
                # Extract route as GeoJSON LineString
                route_result = db.session.execute(
                    db.select(
                        func.ST_AsGeoJSON(func.ST_GeomFromWKB(movement.route_geometry))
                    )
                ).scalar()
                
                if route_result:
                    # Parse the GeoJSON string to a dict
                    import json
                    route_geojson = json.loads(route_result)
            
            movement_dict = {
                'id': movement.id,
                'start_time': movement.start_time.isoformat() if movement.start_time else None,
                'end_time': movement.end_time.isoformat() if movement.end_time else None,
                'duration_minutes': movement.duration_minutes,
                'start_latitude': start_lat,
                'start_longitude': start_lng,
                'end_latitude': end_lat,
                'end_longitude': end_lng,
                'activity_type': movement.activity_type,
                'movement_type': movement.movement_type,
                'source': movement.source,
                'distance_meters': movement.distance_meters,
                'confidence': movement.confidence,
                'has_route': movement.route_geometry is not None,
                'preceding_visit_id': movement.preceding_visit_id,
                'following_visit_id': movement.following_visit_id
            }
            
            # Only include route_geojson if requested and available
            if include_routes:
                movement_dict['route_geojson'] = route_geojson
            
            movements_data.append(movement_dict)
        
        return jsonify({
            'movements': movements_data,
            'count': len(movements_data),
            'filters_applied': filters
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_movements: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500


@bp.route('/movements/<int:movement_id>')
def get_movement_detail(movement_id):
    """
    Get detailed movement information with full route geometry
    
    Args:
        movement_id: Movement ID
    
    Returns:
        JSON response with movement details including route as GeoJSON LineString
    """
    try:
        # Get movement
        query = db.select(Movement).where(Movement.id == movement_id)
        movement = db.session.execute(query).scalar_one_or_none()
        
        if not movement:
            return jsonify({'error': f'Movement {movement_id} not found', 'status': 404}), 404
        
        # Get start coordinates
        if movement.start_location:
            start_coords = db.session.execute(
                db.select(
                    func.ST_Y(func.ST_GeomFromWKB(movement.start_location)).label('lat'),
                    func.ST_X(func.ST_GeomFromWKB(movement.start_location)).label('lng')
                )
            ).first()
            start_lat, start_lng = start_coords.lat, start_coords.lng
        else:
            start_lat, start_lng = None, None
        
        # Get end coordinates
        if movement.end_location:
            end_coords = db.session.execute(
                db.select(
                    func.ST_Y(func.ST_GeomFromWKB(movement.end_location)).label('lat'),
                    func.ST_X(func.ST_GeomFromWKB(movement.end_location)).label('lng')
                )
            ).first()
            end_lat, end_lng = end_coords.lat, end_coords.lng
        else:
            end_lat, end_lng = None, None
        
        # Get route geometry as GeoJSON
        route_geojson = None
        if movement.route_geometry:
            geojson_str = db.session.execute(
                db.select(func.ST_AsGeoJSON(func.ST_GeomFromWKB(movement.route_geometry)))
            ).scalar()
            
            if geojson_str:
                import json
                route_geojson = json.loads(geojson_str)
        
        # Calculate speed if possible
        speed_kmh = None
        if movement.distance_meters and movement.duration_minutes and movement.duration_minutes > 0:
            speed_kmh = round((movement.distance_meters / movement.duration_minutes) * 60 / 1000, 1)
        
        return jsonify({
            'id': movement.id,
            'start_time': movement.start_time.isoformat() if movement.start_time else None,
            'end_time': movement.end_time.isoformat() if movement.end_time else None,
            'duration_minutes': movement.duration_minutes,
            'start_latitude': start_lat,
            'start_longitude': start_lng,
            'end_latitude': end_lat,
            'end_longitude': end_lng,
            'activity_type': movement.activity_type,
            'movement_type': movement.movement_type,
            'source': movement.source,
            'distance_meters': movement.distance_meters,
            'distance_km': round(movement.distance_meters / 1000, 2) if movement.distance_meters else None,
            'confidence': movement.confidence,
            'speed_kmh': speed_kmh,
            'route_geojson': route_geojson,
            'source_metadata': movement.source_metadata,
            'preceding_visit_id': movement.preceding_visit_id,
            'following_visit_id': movement.following_visit_id,
            'imported_at': movement.imported_at.isoformat() if movement.imported_at else None
        })
    
    except Exception as e:
        current_app.logger.error(f"Error in get_movement_detail: {str(e)}")
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
