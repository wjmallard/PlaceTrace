#!/usr/bin/env python3
"""
4_import_photos.py

Import photos from Google Takeout directories WITHOUT geocoding.
- Multiprocessing: hash → check → extract in parallel workers
- Streaming insertions: insert as results arrive (Ctrl-C safe)
- Timezone-aware datetime extraction with priority system
- EXIF extraction for camera metadata
- JSON sidecar parsing for timestamps and GPS
- Resume capability via hash checking
- Extracts local_date and local_time from timezone-aware timestamps

DATETIME PRIORITY:
1. JSON sidecar (timezone-aware) - BEST
2. EXIF + GPS (infer timezone) - GOOD
3. EXIF only (naive, no timezone) - LIMITED
4. Nothing available - NULL

Usage:
    python 4_import_photos.py
"""

import orjson
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from tqdm import tqdm
import sys
from multiprocessing import Pool
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from timezonefinder import TimezoneFinder

from placetrace.db import get_main_connection
from placetrace.config import config

# Enable HEIF/AVIF support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass  # HEIF/AVIF support not available


def make_json_serializable(obj):
    """
    Recursively convert EXIF data to JSON-serializable types.
    Strips null bytes which PostgreSQL JSONB can't handle.
    """
    if isinstance(obj, bytes):
        try:
            return obj.decode('utf-8', errors='ignore').replace('\x00', '')
        except:
            return str(obj).replace('\x00', '')
    elif isinstance(obj, str):
        return obj.replace('\x00', '')  # Strip null bytes
    elif hasattr(obj, 'numerator'):  # IFDRational
        return float(obj)
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    else:
        return obj


def extract_exif(image_path):
    """Extract EXIF data from an image file."""
    try:
        img = Image.open(image_path)
        exif_data = img.getexif()  # Modern method, not _getexif()
        
        if not exif_data:
            return None
        
        # Decode EXIF tags
        exif = {}
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            exif[tag] = value
        
        # Clean all data for JSON serialization
        exif = make_json_serializable(exif)
        
        # Extract GPS data if present - use get_ifd() for GPS IFD
        gps_data = None
        try:
            from PIL.ExifTags import IFD
            gps_ifd = exif_data.get_ifd(IFD.GPSInfo)
            if gps_ifd:
                gps_info = {}
                for key, val in gps_ifd.items():
                    gps_tag = GPSTAGS.get(key, key)
                    gps_info[gps_tag] = val
                gps_data = parse_gps(gps_info)
        except:
            # Fallback to old method if get_ifd not available
            if 'GPSInfo' in exif:
                gps_info = {}
                for key, val in exif['GPSInfo'].items():
                    gps_tag = GPSTAGS.get(key, key)
                    gps_info[gps_tag] = val
                gps_data = parse_gps(gps_info)
        
        # Extract relevant fields
        result = {
            'datetime_original': extract_datetime(exif),
            'camera_make': exif.get('Make'),
            'camera_model': exif.get('Model'),
            'lens_model': exif.get('LensModel'),
            'focal_length': exif.get('FocalLength'),
            'aperture': exif.get('FNumber'),
            'shutter_speed': exif.get('ExposureTime'),
            'iso': exif.get('ISOSpeedRatings') or exif.get('ISO'),
            'flash': exif.get('Flash'),
            'gps_latitude': gps_data['latitude'] if gps_data else None,
            'gps_longitude': gps_data['longitude'] if gps_data else None,
            'gps_altitude': gps_data['altitude'] if gps_data else None,
        }
        
        return result
        
    except Exception as e:
        return None


def extract_datetime(exif):
    """Extract and parse datetime from EXIF data - returns NAIVE datetime (no timezone)."""
    datetime_fields = ['DateTimeOriginal', 'DateTimeDigitized', 'DateTime']
    
    for field in datetime_fields:
        if field in exif:
            try:
                dt_str = str(exif[field])
                # EXIF datetime has NO timezone info - return naive datetime
                dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                return dt  # Naive datetime
            except (ValueError, AttributeError):
                continue
    
    return None


def parse_gps(gps_info):
    """Parse GPS coordinates from EXIF GPS info."""
    try:
        def convert_to_degrees(value):
            d, m, s = value
            return float(d) + float(m) / 60.0 + float(s) / 3600.0
        
        lat = None
        lon = None
        alt = None
        
        if 'GPSLatitude' in gps_info and 'GPSLatitudeRef' in gps_info:
            lat = convert_to_degrees(gps_info['GPSLatitude'])
            if gps_info['GPSLatitudeRef'] == 'S':
                lat = -lat
        
        if 'GPSLongitude' in gps_info and 'GPSLongitudeRef' in gps_info:
            lon = convert_to_degrees(gps_info['GPSLongitude'])
            if gps_info['GPSLongitudeRef'] == 'W':
                lon = -lon
        
        if 'GPSAltitude' in gps_info:
            alt = float(gps_info['GPSAltitude'])
            if gps_info.get('GPSAltitudeRef') == 1:
                alt = -alt
        
        if lat is not None and lon is not None:
            return {'latitude': lat, 'longitude': lon, 'altitude': alt}
        
        return None
        
    except (KeyError, ValueError, TypeError):
        return None


def parse_sidecar(sidecar_path):
    """Parse Google Photos sidecar JSON file."""
    try:
        with open(sidecar_path, 'rb') as f:
            data = orjson.loads(f.read())
        
        result = {
            'taken_timestamp': None,
            'geo_latitude': None,
            'geo_longitude': None,
            'google_photo_id': None,
        }
        
        # Parse timestamp (already in UTC)
        photo_taken = data.get('photoTakenTime', {})
        if 'timestamp' in photo_taken:
            ts = int(photo_taken['timestamp'])
            result['taken_timestamp'] = datetime.fromtimestamp(ts, tz=timezone.utc)
        
        # Parse geo data
        geo_data = data.get('geoData', {})
        if 'latitude' in geo_data and 'longitude' in geo_data:
            result['geo_latitude'] = float(geo_data['latitude'])
            result['geo_longitude'] = float(geo_data['longitude'])
        
        # Extract Google Photo ID from URL
        url = data.get('url', '')
        if '/photos/' in url:
            result['google_photo_id'] = url.split('/photos/')[-1].split('?')[0]
        
        return result
        
    except (IOError, KeyError, ValueError):
        return None


def determine_photo_datetime(exif_dt, sidecar_data, latitude, longitude, file_path):
    """
    Determine best timestamp for photo with quality tracking.
    
    Priority order:
    1. JSON sidecar (has timezone) - BEST
    2. EXIF + GPS coordinates (infer timezone) - GOOD
    3. EXIF only (no timezone available) - LIMITED
    4. Nothing available - NULL
    
    Args:
        exif_dt: Naive datetime from EXIF (no timezone)
        sidecar_data: Dict from parse_sidecar() or None
        latitude: GPS latitude or None
        longitude: GPS longitude or None
        file_path: Path for logging
    
    Returns: (capture_datetime, datetime_source)
        capture_datetime: Timezone-aware datetime or None
        datetime_source: 'json_sidecar', 'exif_gps_tz', 'exif_naive', or None
    """
    
    # Case 1: Has JSON sidecar timestamp (BEST - already timezone-aware UTC)
    if sidecar_data and sidecar_data.get('taken_timestamp'):
        return (sidecar_data['taken_timestamp'], 'json_sidecar')
    
    # Case 2: No JSON, but has EXIF + GPS (GOOD - can infer timezone)
    if exif_dt and latitude and longitude:
        try:
            tf = TimezoneFinder()
            timezone_str = tf.timezone_at(lat=latitude, lng=longitude)
            
            if timezone_str:
                # Convert naive EXIF datetime to timezone-aware using zoneinfo
                tz = ZoneInfo(timezone_str)
                capture_dt = exif_dt.replace(tzinfo=tz)
                return (capture_dt, 'exif_gps_tz')
        except Exception as e:
            # timezonefinder failed - fall through to naive
            pass
    
    # Case 3 & 4: Has EXIF but no GPS, or no datetime at all
    # Store NULL for capture_datetime (we can't determine timezone)
    return (None, 'exif_naive' if exif_dt else None)


def extract_local_date_time(capture_datetime, exif_datetime, latitude=None, longitude=None):
    """
    Extract local date and time for database storage.
    
    Priority:
    1. If capture_datetime exists (timezone-aware), extract local date/time from it
       - If it's in UTC and we have GPS coords, convert to local timezone first
    2. If only exif_datetime exists (naive), use that
    3. Otherwise return None, None
    
    Args:
        capture_datetime: Timezone-aware datetime or None
        exif_datetime: Naive datetime or None
        latitude: GPS latitude or None (for timezone conversion)
        longitude: GPS longitude or None (for timezone conversion)
    
    Returns: (local_date, local_time) tuple
    """
    if capture_datetime:
        # If datetime is in UTC and we have GPS, convert to local timezone
        if capture_datetime.tzinfo == timezone.utc and latitude and longitude:
            try:
                tf = TimezoneFinder()
                timezone_str = tf.timezone_at(lat=latitude, lng=longitude)
                if timezone_str:
                    local_tz = ZoneInfo(timezone_str)
                    capture_datetime = capture_datetime.astimezone(local_tz)
            except Exception:
                # Couldn't convert - use UTC
                pass
        
        # Extract local date/time (now in proper timezone)
        return capture_datetime.date(), capture_datetime.time()
    elif exif_datetime:
        # Have naive EXIF datetime - use as local time
        return exif_datetime.date(), exif_datetime.time()
    else:
        # No datetime available
        return None, None


def compute_file_hash(file_path):
    """Compute SHA256 hash of file"""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_existing_photos(conn):
    """
    Get existing photos for resume capability.
    Returns two sets:
    - existing_hashes: Set of file hashes (for duplicate detection)
    - existing_quick: Set of (filepath, size) tuples (for fast resume check)
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            file_hash,
            file_path,
            file_size_bytes
        FROM Photos
    """)

    existing_hashes = set()
    existing_quick = set()

    for row in cursor.fetchall():
        if row['file_hash']:
            existing_hashes.add(row['file_hash'])

            if row['file_path']:
                existing_quick.add((
                    row['file_path'],
                    row['file_size_bytes'],
                ))
    
    cursor.close()
    return existing_hashes, existing_quick


def process_single_photo(args):
    """
    Process a single photo in a worker process.
    Steps: quick check → hash → full check → extract (if new)
    
    Always returns metadata dict (even with NULL fields) unless already exists.
    This ensures ALL files get catalogued, not just those with EXIF.
    
    Returns:
        dict with metadata, or None if already exists in database
    """
    photo_path, existing_hashes, existing_quick = args
    
    try:
        # Step 1: Fast check using file metadata (stat is instant)
        file_stat = photo_path.stat()
        quick_key = (str(photo_path), file_stat.st_size)
        
        if quick_key in existing_quick:
            # File unchanged, already in database - skip without hashing
            return None
        
        # Step 2: Hash (slower, but only if fast check failed)
        file_hash = compute_file_hash(photo_path)
        
        # Step 3: Check hash (duplicate detection)
        if file_hash in existing_hashes:
            return None
        
        # Step 4: Extract metadata (slow - only for new files)
        file_size = file_stat.st_size
        file_mtime = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
        
        # Get EXIF data (PRIMARY SOURCE) - might be None
        exif = extract_exif(photo_path)
        
        # Parse sidecar JSON if exists (SECONDARY SOURCE) - might be None
        # Google Takeout uses .supplemental-metadata.json extension
        json_path = None
        for pattern in ['.supplemental-metadata.json', '.json']:
            candidate = Path(str(photo_path) + pattern)
            if candidate.exists():
                json_path = candidate
                break
        
        sidecar = {}
        if json_path:
            sidecar = parse_sidecar(json_path) or {}
        
        # Extract naive EXIF datetime (no timezone)
        exif_dt = exif.get('datetime_original') if exif else None
        
        # GPS coordinates: EXIF first, sidecar fallback
        latitude = None
        longitude = None
        if exif and exif.get('gps_latitude') is not None:
            latitude = exif['gps_latitude']
            longitude = exif['gps_longitude']
        elif sidecar.get('geo_latitude') is not None:
            latitude = sidecar['geo_latitude']
            longitude = sidecar['geo_longitude']
        
        # Reject invalid coordinates
        # (0, 0) is "Null Island" - a placeholder for missing GPS data
        if latitude == 0 and longitude == 0:
            latitude = None
            longitude = None
        
        # Determine best capture_datetime using decision tree
        capture_datetime, datetime_source = determine_photo_datetime(
            exif_dt, sidecar, latitude, longitude, photo_path
        )
        
        # Extract local date and time
        local_date, local_time = extract_local_date_time(capture_datetime, exif_dt, latitude, longitude)
        
        # Camera metadata (EXIF only - all might be None)
        camera_make = exif.get('camera_make') if exif else None
        camera_model = exif.get('camera_model') if exif else None
        lens_model = exif.get('lens_model') if exif else None
        focal_length = exif.get('focal_length') if exif else None
        aperture = exif.get('aperture') if exif else None
        shutter_speed = exif.get('shutter_speed') if exif else None
        iso = exif.get('iso') if exif else None
        flash = exif.get('flash') if exif else None
        
        # Flash fired detection
        flash_fired = None
        if flash is not None:
            flash_fired = bool(flash & 0x1)
        
        # Always return metadata dict, even if most fields are NULL
        # This ensures we catalog ALL files, not just those with EXIF
        return {
            'file_path': str(photo_path),
            'file_hash': file_hash,
            'file_size_bytes': file_size,
            'file_mtime': file_mtime,
            'capture_datetime': capture_datetime,
            'exif_datetime': exif_dt,  # Naive EXIF datetime
            'datetime_source': datetime_source,
            'local_date': local_date,
            'local_time': local_time,
            'latitude': latitude,
            'longitude': longitude,
            'camera_make': camera_make,
            'camera_model': camera_model,
            'lens_model': lens_model,
            'focal_length_mm': focal_length,
            'aperture_f_number': aperture,
            'shutter_speed_seconds': shutter_speed,
            'iso': iso,
            'flash_fired': flash_fired,
            'sidecar_latitude': sidecar.get('geo_latitude'),
            'sidecar_longitude': sidecar.get('geo_longitude'),
            'google_photo_id': sidecar.get('google_photo_id'),
        }
    except Exception as e:
        # Even on error, return a minimal metadata dict so file gets catalogued
        # Only the hash and path are guaranteed
        try:
            file_stat = photo_path.stat()
            quick_key = (str(photo_path), file_stat.st_size)
            
            if quick_key in existing_quick:
                return None
                
            file_hash = compute_file_hash(photo_path)
            if file_hash in existing_hashes:
                return None
            
            return {
                'file_path': str(photo_path),
                'file_hash': file_hash,
                'file_size_bytes': file_stat.st_size,
                'file_mtime': datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc),
                'capture_datetime': None,
                'exif_datetime': None,
                'datetime_source': None,
                'local_date': None,
                'local_time': None,
                'latitude': None,
                'longitude': None,
                'camera_make': None,
                'camera_model': None,
                'lens_model': None,
                'focal_length_mm': None,
                'aperture_f_number': None,
                'shutter_speed_seconds': None,
                'iso': None,
                'flash_fired': None,
                'sidecar_latitude': None,
                'sidecar_longitude': None,
                'google_photo_id': None,
            }
        except:
            # Complete failure - skip this file
            return None


def find_photo_files(directories):
    """Recursively find all photo files in directories"""
    photo_extensions = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw'}
    
    photos = []
    for directory in directories:
        path = Path(directory).expanduser()
        if not path.exists():
            print(f"Warning: Directory not found: {path}")
            continue
        
        for file_path in path.rglob('*'):
            if file_path.suffix.lower() in photo_extensions:
                photos.append(file_path)
    
    return photos


def import_photos(conn, photo_directories):
    """
    Import photos with streaming insertion.
    Multiprocessing: hash → check → extract in parallel
    Main thread: insert as results arrive
    """
    print(f"Scanning directories for photos...")
    photo_files = find_photo_files(photo_directories)
    print(f"Found {len(photo_files):,} photo files")
    
    if not photo_files:
        print("No photos found!")
        return 0
    
    # Get existing photos (for resume capability)
    print("Checking for existing photos in database...")
    existing_hashes, existing_quick = get_existing_photos(conn)
    print(f"Found {len(existing_hashes):,} photos already in database")
    
    # Setup multiprocessing
    num_workers = config['processing'].get('num_workers', 4)
    print(f"Using {num_workers} workers for parallel processing")
    
    # Prepare worker arguments
    worker_args = [(photo_path, existing_hashes, existing_quick) for photo_path in photo_files]
    
    # Process and insert (streaming)
    print("\nProcessing and inserting photos (streaming)...")
    cursor = conn.cursor()
    inserted = 0
    skipped = 0
    
    try:
        with Pool(num_workers) as pool:
            # imap_unordered returns results as they complete
            for metadata in tqdm(
                pool.imap_unordered(process_single_photo, worker_args, chunksize=10),
                total=len(photo_files),
                desc="Progress"
            ):
                # Skip None results (already in DB or failed)
                if metadata is None:
                    skipped += 1
                    continue
                
                # Insert immediately
                try:
                    # Create savepoint before this insert
                    cursor.execute("SAVEPOINT insert_photo")
                    
                    cursor.execute("""
                        INSERT INTO Photos (
                            file_path, file_hash, file_size_bytes, file_mtime,
                            capture_datetime, exif_datetime, datetime_source,
                            local_date, local_time,
                            latitude, longitude, location_id,
                            camera_make, camera_model, lens_model,
                            focal_length_mm, aperture_f_number, shutter_speed_seconds,
                            iso, flash_fired,
                            sidecar_latitude, sidecar_longitude,
                            google_photo_id
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s, NULL,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s,
                            %s
                        )
                    """, (
                        metadata['file_path'],
                        metadata['file_hash'],
                        metadata['file_size_bytes'],
                        metadata['file_mtime'],
                        metadata['capture_datetime'],
                        metadata['exif_datetime'],
                        metadata['datetime_source'],
                        metadata['local_date'],
                        metadata['local_time'],
                        metadata['latitude'],
                        metadata['longitude'],
                        metadata['camera_make'],
                        metadata['camera_model'],
                        metadata['lens_model'],
                        metadata['focal_length_mm'],
                        metadata['aperture_f_number'],
                        metadata['shutter_speed_seconds'],
                        metadata['iso'],
                        metadata['flash_fired'],
                        metadata['sidecar_latitude'],
                        metadata['sidecar_longitude'],
                        metadata['google_photo_id'],
                    ))
                    
                    # Release savepoint (marks insert as successful)
                    cursor.execute("RELEASE SAVEPOINT insert_photo")
                    inserted += 1
                    
                    # Commit every 100 photos
                    if inserted % 100 == 0:
                        conn.commit()
                        
                except Exception as e:
                    # Handle duplicate hash (race condition between workers)
                    if 'duplicate key' in str(e) or 'UniqueViolation' in str(type(e)):
                        skipped += 1
                        # Rollback to savepoint (only affects this one insert)
                        cursor.execute("ROLLBACK TO SAVEPOINT insert_photo")
                        cursor.execute("RELEASE SAVEPOINT insert_photo")
                        continue
                    else:
                        # Re-raise unexpected errors
                        raise
    
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user (Ctrl-C)")
        print("Committing current batch...")
        conn.commit()
        print(f"✓ Safely committed {inserted:,} photos before interruption")
        print(f"✓ Resume by running script again - it will skip already-imported photos")
        cursor.close()
        return inserted
    
    # Final commit
    conn.commit()
    cursor.close()
    
    print(f"\n✓ Inserted {inserted:,} new photos")
    print(f"⊘ Skipped {skipped:,} photos (already in database)")
    print(f"\nNote: All files catalogued, even without EXIF metadata")
    print(f"      location_id is NULL - run 4_geocode.py to populate")
    
    return inserted


def print_summary(conn):
    """Print summary statistics"""
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as count FROM Photos")
    total = cursor.fetchone()['count']
    
    cursor.execute("""
        SELECT COUNT(*) as count FROM Photos 
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """)
    with_gps = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Photos WHERE location_id IS NULL")
    ungeocoded = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Photos WHERE capture_datetime IS NOT NULL")
    with_datetime = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM Photos WHERE local_date IS NOT NULL")
    with_local_time = cursor.fetchone()['count']
    
    cursor.execute("""
        SELECT datetime_source, COUNT(*) as count
        FROM Photos
        GROUP BY datetime_source
        ORDER BY 
            CASE datetime_source
                WHEN 'json_sidecar' THEN 1
                WHEN 'exif_gps_tz' THEN 2
                WHEN 'exif_naive' THEN 3
                ELSE 4
            END
    """)
    by_source = cursor.fetchall()
    
    cursor.execute("""
        SELECT COUNT(*) as count FROM Photos 
        WHERE camera_make IS NOT NULL
    """)
    with_camera = cursor.fetchone()['count']
    
    # Count files with NO metadata at all (likely screenshots/downloads)
    cursor.execute("""
        SELECT COUNT(*) as count FROM Photos 
        WHERE capture_datetime IS NULL 
          AND latitude IS NULL 
          AND camera_make IS NULL
    """)
    no_metadata = cursor.fetchone()['count']
    
    cursor.close()
    
    print("\n" + "="*60)
    print("PHOTOS IMPORT SUMMARY")
    print("="*60)
    print(f"\nTotal photos:        {total:>8,}")
    print(f"With GPS coords:     {with_gps:>8,} ({with_gps/total*100 if total > 0 else 0:.1f}%)")
    print(f"With datetime:       {with_datetime:>8,} ({with_datetime/total*100 if total > 0 else 0:.1f}%)")
    print(f"With local date:     {with_local_time:>8,} ({with_local_time/total*100 if total > 0 else 0:.1f}%)")
    
    print(f"\nDatetime sources:")
    for row in by_source:
        source = row['datetime_source'] or 'NULL'
        count = row['count']
        pct = count/total*100 if total > 0 else 0
        print(f"  {source:<20} {count:>8,} ({pct:>5.1f}%)")
    
    print(f"\nWith camera info:    {with_camera:>8,} ({with_camera/total*100 if total > 0 else 0:.1f}%)")
    print(f"No metadata:         {no_metadata:>8,} ({no_metadata/total*100 if total > 0 else 0:.1f}%) [likely screenshots]")
    print(f"Needs geocoding:     {ungeocoded:>8,}")
    print("="*60 + "\n")


def main():
    """Main execution flow"""
    print("="*60)
    print("IMPORT PHOTOS")
    print("="*60)
    
    # Get photo directories from config
    photo_directories = config['source_data']['photo_directories']
    
    # Connect to database
    conn = get_main_connection()
    
    try:
        # Import photos
        imported = import_photos(conn, photo_directories)
        
        # Print summary
        print_summary(conn)
        
        print("✓ Photo import complete!")
        
    except Exception as e:
        print(f"\n✗ Error during import: {e}", file=sys.stderr)
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
