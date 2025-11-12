--
-- Unified Location & Photo Archive Database Schema
-- PostgreSQL with PostGIS
--

-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================================================
-- Locations Table - Normalized location dictionary
-- ============================================================================

CREATE TABLE Locations (
    id SERIAL PRIMARY KEY,
    
    -- Administrative hierarchy (all levels optional except country)
    city TEXT,                          -- admin_level 8
    county TEXT,                        -- admin_level 6
    state TEXT,                         -- admin_level 4
    country TEXT NOT NULL,              -- admin_level 2
    
    -- OSM boundary IDs for each level
    city_osm_id BIGINT,
    county_osm_id BIGINT,
    state_osm_id BIGINT,
    country_osm_id BIGINT,
    
    -- Primary admin level and centroid
    admin_level INTEGER NOT NULL,       -- Highest detail level (8, 6, 4, or 2)
    centroid GEOGRAPHY(POINT, 4326),    -- Center of primary boundary
    
    -- Ensure uniqueness across full hierarchy
    UNIQUE(city, county, state, country)
);

-- Indexes for Locations
CREATE INDEX idx_locations_city ON Locations(city) WHERE city IS NOT NULL;
CREATE INDEX idx_locations_state ON Locations(state) WHERE state IS NOT NULL;
CREATE INDEX idx_locations_country ON Locations(country);
CREATE INDEX idx_locations_admin_level ON Locations(admin_level);
CREATE INDEX idx_locations_centroid ON Locations USING GIST(centroid);

-- ============================================================================
-- Visits Table - Location timeline entries
-- ============================================================================

CREATE TABLE Visits (
    id BIGSERIAL PRIMARY KEY,
    
    -- Temporal bounds (timezone-aware, moment in time)
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_minutes INTEGER,
    
    -- Local time representation (wall-clock time at visit location)
    local_date DATE,                    -- Calendar date in visit's timezone
    local_time TIME,                    -- Wall-clock time in visit's timezone
    
    -- Spatial data (full precision)
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    location_id INTEGER REFERENCES Locations(id),
    
    -- Visit classification
    visit_type TEXT,                    -- 'timeline', 'photo_session', 'home', 'work'
    semantic_type TEXT,                 -- Google's classification (HOME, WORK, etc.)
    
    -- Google Timeline metadata
    place_id TEXT                       -- Google Place ID (if available)
);

-- Indexes for Visits
CREATE INDEX idx_visits_start_time ON Visits(start_time);
CREATE INDEX idx_visits_end_time ON Visits(end_time);
CREATE INDEX idx_visits_local_date ON Visits(local_date);
CREATE INDEX idx_visits_location ON Visits USING GIST(location);
CREATE INDEX idx_visits_location_id ON Visits(location_id);
CREATE INDEX idx_visits_visit_type ON Visits(visit_type);

-- ============================================================================
-- Movements Table - Travel/activity between locations
-- ============================================================================

CREATE TABLE Movements (
    id BIGSERIAL PRIMARY KEY,
    
    -- Temporal bounds (timezone-aware, moment in time)
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_minutes INTEGER,
    
    -- Local time representation (wall-clock time at movement start location)
    local_date DATE,                    -- Calendar date in movement's timezone
    local_time TIME,                    -- Wall-clock time in movement's timezone
    
    -- Spatial data
    start_location GEOGRAPHY(POINT, 4326) NOT NULL,
    end_location GEOGRAPHY(POINT, 4326) NOT NULL,
    route_geometry GEOGRAPHY(LINESTRING, 4326),  -- Full path/route
    
    -- Movement characteristics
    activity_type TEXT,                 -- 'WALKING', 'IN_VEHICLE', 'CYCLING', 'FLYING', etc. (nullable for breadcrumbs)
    confidence DOUBLE PRECISION,        -- Confidence in activity_type (0-1, nullable)
    distance_meters DOUBLE PRECISION,
    
    -- Data source and type
    source TEXT NOT NULL,               -- 'google_timeline', 'strava', 'garmin', 'apple_health', 'manual'
    movement_type TEXT NOT NULL,        -- 'activity', 'breadcrumb_trail', 'gps_track', 'inferred'
    
    -- Source-specific metadata (flexible JSONB storage)
    source_metadata JSONB,              -- Store format-specific fields (edit_confirmation, parking_event, etc.)
    
    -- Links to visits (for trip detection)
    preceding_visit_id BIGINT REFERENCES Visits(id),
    following_visit_id BIGINT REFERENCES Visits(id),
    
    -- Processing metadata
    imported_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for Movements
CREATE INDEX idx_movements_start_time ON Movements(start_time);
CREATE INDEX idx_movements_end_time ON Movements(end_time);
CREATE INDEX idx_movements_local_date ON Movements(local_date);
CREATE INDEX idx_movements_activity_type ON Movements(activity_type) WHERE activity_type IS NOT NULL;
CREATE INDEX idx_movements_source ON Movements(source);
CREATE INDEX idx_movements_movement_type ON Movements(movement_type);
CREATE INDEX idx_movements_preceding_visit ON Movements(preceding_visit_id);
CREATE INDEX idx_movements_following_visit ON Movements(following_visit_id);
CREATE INDEX idx_movements_route ON Movements USING GIST(route_geometry) WHERE route_geometry IS NOT NULL;

-- ============================================================================
-- Photos Table - Photo archive with metadata
-- ============================================================================

CREATE TABLE Photos (
    id SERIAL PRIMARY KEY,
    
    -- File information
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    file_mtime TIMESTAMP,               -- File modification time (for fast resume checks)
    original_filename TEXT,
    file_size_bytes BIGINT,
    media_type TEXT,                    -- 'image/jpeg', 'image/png', etc.
    
    -- Image properties
    width INTEGER,
    height INTEGER,
    
    -- Temporal data (timezone-aware, best available source)
    capture_datetime TIMESTAMP WITH TIME ZONE,
    exif_datetime TIMESTAMP,                -- Naive EXIF datetime (no timezone)
    datetime_source TEXT CHECK (datetime_source IN 
        ('json_sidecar', 'exif_gps_tz', 'exif_naive', NULL)
    ),
    
    -- Local time representation (wall-clock time at photo location)
    local_date DATE,                    -- Calendar date in photo's timezone
    local_time TIME,                    -- Wall-clock time in photo's timezone
    
    -- Spatial data (from EXIF, NULL for ~50% of photos)
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    location_id INTEGER REFERENCES Locations(id),
    
    -- Link to visit
    visit_id BIGINT REFERENCES Visits(id),
    
    -- Camera metadata (from EXIF)
    camera_make TEXT,
    camera_model TEXT,
    lens_model TEXT,
    focal_length_mm DOUBLE PRECISION,
    aperture_f_number DOUBLE PRECISION,
    shutter_speed_seconds DOUBLE PRECISION,
    iso INTEGER,
    flash_fired BOOLEAN,
    
    -- Sidecar metadata (from Google Photos JSON, for reference)
    sidecar_latitude DOUBLE PRECISION,
    sidecar_longitude DOUBLE PRECISION,
    google_photo_id TEXT,
    
    -- Processing metadata
    imported_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    thumbnail_path TEXT
);

-- Indexes for Photos
CREATE INDEX idx_photos_capture_datetime ON Photos(capture_datetime);
CREATE INDEX idx_photos_local_date ON Photos(local_date);
CREATE INDEX idx_photos_location_id ON Photos(location_id) WHERE location_id IS NOT NULL;
CREATE INDEX idx_photos_visit_id ON Photos(visit_id) WHERE visit_id IS NOT NULL;
CREATE INDEX idx_photos_file_hash ON Photos(file_hash);
CREATE INDEX idx_photos_thumbnail_path ON Photos(thumbnail_path) WHERE thumbnail_path IS NULL;

-- Spatial index for photos with coordinates
CREATE INDEX idx_photos_spatial ON Photos 
    USING GIST(ST_SetSRID(ST_MakePoint(longitude, latitude), 4326))
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL;

-- ============================================================================
-- Trips Table - Detected trip records
-- ============================================================================

CREATE TABLE Trips (
    id BIGSERIAL PRIMARY KEY,
    
    -- Temporal bounds
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    
    -- Trip classification
    trip_category TEXT NOT NULL,        -- 'day', 'short', 'long'
    
    -- Location information
    cities TEXT[],                      -- ['Boston', 'Cambridge'] for multi-city
    primary_location_id INTEGER REFERENCES Locations(id),
    display_name TEXT,                  -- Human-readable trip name
    
    UNIQUE(start_time, end_time)
);

-- Indexes for Trips
CREATE INDEX idx_trips_start_time ON Trips(start_time);
CREATE INDEX idx_trips_category ON Trips(trip_category);

-- ============================================================================
-- Junction Tables - Many-to-many relationships
-- ============================================================================

CREATE TABLE Trip_Visits (
    trip_id BIGINT REFERENCES Trips(id) ON DELETE CASCADE,
    visit_id BIGINT REFERENCES Visits(id) ON DELETE CASCADE,
    PRIMARY KEY (trip_id, visit_id)
);

CREATE TABLE Trip_Photos (
    trip_id BIGINT REFERENCES Trips(id) ON DELETE CASCADE,
    photo_id INTEGER REFERENCES Photos(id) ON DELETE CASCADE,
    PRIMARY KEY (trip_id, photo_id)
);

-- Indexes for junction tables
CREATE INDEX idx_trip_visits_trip_id ON Trip_Visits(trip_id);
CREATE INDEX idx_trip_visits_visit_id ON Trip_Visits(visit_id);
CREATE INDEX idx_trip_photos_trip_id ON Trip_Photos(trip_id);
CREATE INDEX idx_trip_photos_photo_id ON Trip_Photos(photo_id);
