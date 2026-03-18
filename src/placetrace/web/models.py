"""
SQLAlchemy models for PlaceTrace database
Maps to PostgreSQL schema with PostGIS extensions
"""

from sqlalchemy import Column, Integer, BigInteger, Text, Float, Boolean, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from geoalchemy2 import Geography
from placetrace.web.database import db


# Junction tables for many-to-many relationships
trip_visits = Table(
    'trip_visits',
    db.Model.metadata,
    Column('trip_id', BigInteger, ForeignKey('trips.id', ondelete='CASCADE'), primary_key=True),
    Column('visit_id', BigInteger, ForeignKey('visits.id', ondelete='CASCADE'), primary_key=True)
)

trip_photos = Table(
    'trip_photos',
    db.Model.metadata,
    Column('trip_id', BigInteger, ForeignKey('trips.id', ondelete='CASCADE'), primary_key=True),
    Column('photo_id', Integer, ForeignKey('photos.id', ondelete='CASCADE'), primary_key=True)
)


class Location(db.Model):
    """Normalized location dictionary with administrative hierarchy"""
    __tablename__ = 'locations'
    
    id = Column(Integer, primary_key=True)
    
    # Administrative hierarchy
    city = Column(Text)
    county = Column(Text)
    state = Column(Text)
    country = Column(Text, nullable=False)
    
    # OSM boundary IDs
    city_osm_id = Column(BigInteger)
    county_osm_id = Column(BigInteger)
    state_osm_id = Column(BigInteger)
    country_osm_id = Column(BigInteger)
    
    # Primary admin level and centroid
    admin_level = Column(Integer, nullable=False)
    centroid = Column(Geography('POINT', srid=4326))
    
    # Relationships
    visits = relationship('Visit', back_populates='location_rel')
    photos = relationship('Photo', back_populates='location_rel')
    trips = relationship('Trip', back_populates='primary_location')
    
    def format_name(self):
        """Format location as human-readable string"""
        parts = []
        
        # Always prefer city if available
        if self.city:
            parts.append(self.city)
        # Fall back to county only if no city
        elif self.county:
            parts.append(self.county)
        
        # Add state if present
        if self.state:
            parts.append(self.state)
        
        # Always add country (required field)
        parts.append(self.country)
        
        return ", ".join(parts)


class Visit(db.Model):
    """Location timeline entries"""
    __tablename__ = 'visits'
    
    id = Column(BigInteger, primary_key=True)
    
    # Temporal bounds
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    duration_minutes = Column(Integer)
    
    # Local time representation
    local_start_date = Column(db.Date)
    local_start_time = Column(db.Time)
    local_end_date = Column(db.Date)
    local_end_time = Column(db.Time)
    
    # Spatial data
    location = Column(Geography('POINT', srid=4326), nullable=False)
    location_id = Column(Integer, ForeignKey('locations.id'))
    
    # Visit classification
    visit_type = Column(Text)
    semantic_type = Column(Text)
    
    # Google Timeline metadata
    place_id = Column(Text)
    
    # Relationships
    location_rel = relationship('Location', back_populates='visits')
    photos = relationship('Photo', back_populates='visit')
    trips = relationship('Trip', secondary=trip_visits, back_populates='visits')
    
    @property
    def latitude(self):
        """Extract latitude from geography point"""
        if self.location:
            return db.session.scalar(db.select(db.func.ST_Y(db.func.ST_GeomFromWKB(self.location))))
        return None
    
    @property
    def longitude(self):
        """Extract longitude from geography point"""
        if self.location:
            return db.session.scalar(db.select(db.func.ST_X(db.func.ST_GeomFromWKB(self.location))))
        return None
    
    @property
    def location_name(self):
        """Get formatted location name"""
        if self.location_rel:
            return self.location_rel.format_name()
        return None


class Photo(db.Model):
    """Photo archive with metadata"""
    __tablename__ = 'photos'
    
    id = Column(Integer, primary_key=True)
    
    # File information
    file_path = Column(Text, nullable=False)
    file_hash = Column(Text, nullable=False, unique=True)
    original_filename = Column(Text)
    file_size_bytes = Column(BigInteger)
    media_type = Column(Text)
    
    # Image properties
    width = Column(Integer)
    height = Column(Integer)
    
    # Temporal data
    capture_datetime = Column(DateTime(timezone=True))
    
    # Spatial data
    latitude = Column(Float)
    longitude = Column(Float)
    location_id = Column(Integer, ForeignKey('locations.id'))
    
    # Link to visit
    visit_id = Column(BigInteger, ForeignKey('visits.id'))
    
    # Camera metadata
    camera_make = Column(Text)
    camera_model = Column(Text)
    lens_model = Column(Text)
    focal_length_mm = Column(Float)
    aperture_f_number = Column(Float)
    shutter_speed_seconds = Column(Float)
    iso = Column(Integer)
    flash_fired = Column(Boolean)
    
    # Sidecar metadata
    sidecar_datetime = Column(DateTime(timezone=True))
    sidecar_latitude = Column(Float)
    sidecar_longitude = Column(Float)
    google_photo_id = Column(Text)
    
    # Processing metadata
    imported_at = Column(DateTime(timezone=True))
    thumbnail_path = Column(Text)
    
    # Relationships
    location_rel = relationship('Location', back_populates='photos')
    visit = relationship('Visit', back_populates='photos')
    trips = relationship('Trip', secondary=trip_photos, back_populates='photos')
    
    @property
    def location_name(self):
        """Get formatted location name"""
        if self.location_rel:
            return self.location_rel.format_name()
        return None
    
    @property
    def exif(self):
        """Return EXIF metadata as dictionary"""
        return {
            'camera_make': self.camera_make,
            'camera_model': self.camera_model,
            'lens_model': self.lens_model,
            'focal_length_mm': self.focal_length_mm,
            'aperture_f_number': self.aperture_f_number,
            'shutter_speed_seconds': self.shutter_speed_seconds,
            'iso': self.iso,
            'flash_fired': self.flash_fired
        }


class Trip(db.Model):
    """Detected trip records"""
    __tablename__ = 'trips'
    
    id = Column(BigInteger, primary_key=True)
    
    # Temporal bounds
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    
    # Local time representation
    local_start_date = Column(db.Date)
    local_start_time = Column(db.Time)
    local_end_date = Column(db.Date)
    local_end_time = Column(db.Time)
    
    # Trip classification
    trip_category = Column(Text, nullable=False)
    
    # Location information
    cities = Column(db.ARRAY(Text))
    primary_location_id = Column(Integer, ForeignKey('locations.id'))
    display_name = Column(Text)
    
    # Relationships
    primary_location = relationship('Location', back_populates='trips')
    visits = relationship('Visit', secondary=trip_visits, back_populates='trips')
    photos = relationship('Photo', secondary=trip_photos, back_populates='trips')


class Movement(db.Model):
    """Travel/activity between locations"""
    __tablename__ = 'movements'
    
    id = Column(BigInteger, primary_key=True)
    
    # Temporal bounds
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    duration_minutes = Column(Integer)
    
    # Local time representation
    local_start_date = Column(db.Date)
    local_start_time = Column(db.Time)
    local_end_date = Column(db.Date)
    local_end_time = Column(db.Time)
    
    # Spatial data
    start_location = Column(Geography('POINT', srid=4326), nullable=False)
    end_location = Column(Geography('POINT', srid=4326), nullable=False)
    route_geometry = Column(Geography('LINESTRING', srid=4326))
    
    # Movement characteristics
    activity_type = Column(Text)
    confidence = Column(Float)
    distance_meters = Column(Float)
    
    # Data source and type
    source = Column(Text, nullable=False)
    movement_type = Column(Text, nullable=False)
    source_metadata = Column(db.JSON)  # Maps to JSONB in PostgreSQL
    
    # Links to visits (for trip detection)
    preceding_visit_id = Column(BigInteger, ForeignKey('visits.id'))
    following_visit_id = Column(BigInteger, ForeignKey('visits.id'))
    
    # Processing metadata
    imported_at = Column(DateTime(timezone=True))
    
    # Relationships
    preceding_visit = relationship('Visit', foreign_keys=[preceding_visit_id])
    following_visit = relationship('Visit', foreign_keys=[following_visit_id])
    
    @property
    def start_latitude(self):
        """Extract latitude from start location"""
        if self.start_location:
            return db.session.scalar(db.select(db.func.ST_Y(db.func.ST_GeomFromWKB(self.start_location))))
        return None
    
    @property
    def start_longitude(self):
        """Extract longitude from start location"""
        if self.start_location:
            return db.session.scalar(db.select(db.func.ST_X(db.func.ST_GeomFromWKB(self.start_location))))
        return None
    
    @property
    def end_latitude(self):
        """Extract latitude from end location"""
        if self.end_location:
            return db.session.scalar(db.select(db.func.ST_Y(db.func.ST_GeomFromWKB(self.end_location))))
        return None
    
    @property
    def end_longitude(self):
        """Extract longitude from end location"""
        if self.end_location:
            return db.session.scalar(db.select(db.func.ST_X(db.func.ST_GeomFromWKB(self.end_location))))
        return None
    
    @property
    def route_geojson(self):
        """Get route geometry as GeoJSON LineString"""
        if self.route_geometry:
            geojson = db.session.scalar(
                db.select(db.func.ST_AsGeoJSON(db.func.ST_GeomFromWKB(self.route_geometry)))
            )
            if geojson:
                import json
                return json.loads(geojson)
        return None
