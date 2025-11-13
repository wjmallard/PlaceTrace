#!/usr/bin/env python3
"""
5_link_photo_visits.py

Link photos to visits using spatio-temporal matching:
1. Match photos to existing visits (eg: ±15min, 100m)
2. Cluster unmatched photos using ST_ClusterDBSCAN (30min, 5m)
3. Create synthetic photo-session visits for clusters
4. Update Photos.visit_id foreign keys

Note: Photo-session visits created with location_id = NULL
      Run geocoding next to populate location_id for all records

Usage:
    python 5_link_photo_visits.py
"""

from tqdm import tqdm
import sys

# Import database module
from db import get_main_connection, config


def match_photos_to_existing_visits(conn):
    """
    Match photos to existing visits using spatio-temporal criteria.
    Returns number of photos matched.
    """
    time_buffer = config['photo_visits']['visit_match_time_buffer_minutes']
    distance_buffer = config['photo_visits']['visit_match_distance_meters']
    
    print(f"\nMatching photos to existing visits (±{time_buffer}min, {distance_buffer}m)...")
    
    cursor = conn.cursor()
    
    # First, get count of photos with GPS coordinates and no visit
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM Photos 
        WHERE latitude IS NOT NULL 
          AND longitude IS NOT NULL 
          AND visit_id IS NULL
    """)
    total_unlinked = cursor.fetchone()['count']
    print(f"Found {total_unlinked:,} photos with GPS coordinates needing linkage")
    
    if total_unlinked == 0:
        print("✓ No photos to match")
        cursor.close()
        return 0
    
    # Spatial-temporal join to find matches
    # For each photo, find the best matching visit (closest in time if multiple matches)
    query = """
        WITH photo_visit_matches AS (
            SELECT 
                p.id AS photo_id,
                v.id AS visit_id,
                ABS(EXTRACT(EPOCH FROM (p.capture_datetime - v.start_time))) AS time_diff_seconds,
                ROW_NUMBER() OVER (
                    PARTITION BY p.id 
                    ORDER BY ABS(EXTRACT(EPOCH FROM (p.capture_datetime - v.start_time)))
                ) AS rank
            FROM Photos p
            JOIN Visits v ON 
                -- Time match: photo within visit time window (with buffer)
                p.capture_datetime BETWEEN 
                    v.start_time - INTERVAL '%s minutes' AND 
                    v.end_time + INTERVAL '%s minutes'
                -- Space match: photo within distance threshold
                AND ST_DWithin(
                    v.location,
                    ST_SetSRID(ST_MakePoint(p.longitude, p.latitude), 4326)::geography,
                    %s
                )
            WHERE p.latitude IS NOT NULL 
              AND p.longitude IS NOT NULL
              AND p.visit_id IS NULL
              AND p.capture_datetime IS NOT NULL
        )
        UPDATE Photos p
        SET visit_id = m.visit_id
        FROM photo_visit_matches m
        WHERE p.id = m.photo_id
          AND m.rank = 1
        RETURNING p.id
    """
    
    cursor.execute(query, (time_buffer, time_buffer, distance_buffer))
    matched_count = cursor.rowcount
    conn.commit()
    
    print(f"✓ Matched {matched_count:,} photos to existing visits")
    cursor.close()
    
    return matched_count


def cluster_unmatched_photos(conn):
    """
    Cluster remaining unmatched photos using ST_ClusterDBSCAN.
    Returns list of clusters with metadata.
    """
    time_window = config['photo_visits']['time_window_minutes']
    distance_threshold = config['photo_visits']['distance_threshold_meters']
    
    print(f"\nClustering unmatched photos (within {time_window}min, {distance_threshold}m)...")
    
    cursor = conn.cursor()
    
    # Count unmatched photos
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM Photos 
        WHERE latitude IS NOT NULL 
          AND longitude IS NOT NULL 
          AND visit_id IS NULL
          AND capture_datetime IS NOT NULL
    """)
    unmatched_count = cursor.fetchone()['count']
    
    if unmatched_count == 0:
        print("✓ No unmatched photos remaining")
        cursor.close()
        return []
    
    print(f"Found {unmatched_count:,} unmatched photos to cluster")
    
    # Use ST_ClusterDBSCAN for spatial clustering combined with temporal windowing
    query = """
        WITH time_ordered_photos AS (
            SELECT 
                id,
                capture_datetime,
                latitude,
                longitude,
                ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography AS location
            FROM Photos
            WHERE latitude IS NOT NULL 
              AND longitude IS NOT NULL 
              AND visit_id IS NULL
              AND capture_datetime IS NOT NULL
            ORDER BY capture_datetime
        ),
        spatial_clusters AS (
            SELECT 
                id,
                capture_datetime,
                latitude,
                longitude,
                location,
                ST_ClusterDBSCAN(
                    location::geometry, 
                    eps := %s,  -- distance threshold in meters
                    minpoints := 1  -- every photo is its own cluster minimum
                ) OVER (ORDER BY capture_datetime) AS spatial_cluster_id
            FROM time_ordered_photos
        ),
        with_lags AS (
            SELECT 
                id,
                capture_datetime,
                latitude,
                longitude,
                location,
                spatial_cluster_id,
                -- Compute LAG values first (can't nest window functions)
                LAG(capture_datetime) OVER (ORDER BY capture_datetime) AS prev_datetime,
                LAG(spatial_cluster_id) OVER (ORDER BY capture_datetime) AS prev_spatial_cluster
            FROM spatial_clusters
        ),
        temporal_spatial_clusters AS (
            SELECT 
                id,
                capture_datetime,
                latitude,
                longitude,
                location,
                spatial_cluster_id,
                -- Create new cluster when time gap > threshold OR spatial cluster changes
                SUM(CASE 
                    WHEN prev_datetime IS NULL THEN 1
                    WHEN capture_datetime - prev_datetime > INTERVAL '%s minutes' THEN 1
                    WHEN spatial_cluster_id != prev_spatial_cluster THEN 1
                    ELSE 0
                END) OVER (ORDER BY capture_datetime) AS cluster_id
            FROM with_lags
        )
        SELECT 
            cluster_id,
            ARRAY_AGG(id ORDER BY capture_datetime) AS photo_ids,
            ST_AsText(ST_Centroid(ST_Collect(location::geometry))) AS centroid_wkt,
            MIN(capture_datetime) AS start_time,
            MAX(capture_datetime) AS end_time,
            COUNT(*) AS photo_count
        FROM temporal_spatial_clusters
        GROUP BY cluster_id
        ORDER BY start_time
    """
    
    cursor.execute(query, (distance_threshold, time_window))
    clusters = cursor.fetchall()
    cursor.close()
    
    print(f"✓ Created {len(clusters):,} photo clusters")
    
    return clusters


def create_photo_session_visits(conn, clusters):
    """
    Create visit records for photo clusters and link photos.
    Does NOT geocode - leaves location_id = NULL for batch geocoding later.
    Returns number of visits created.
    """
    if not clusters:
        return 0
    
    print(f"\nCreating photo-session visits for {len(clusters):,} clusters...")
    
    cursor = conn.cursor()
    visits_created = 0
    photos_linked = 0
    failed_clusters = 0
    
    try:
        # Process clusters with progress bar
        for cluster in tqdm(clusters, desc="Creating visits"):
            try:
                cluster_id = cluster['cluster_id']
                photo_ids = cluster['photo_ids']
                centroid_wkt = cluster['centroid_wkt']
                start_time = cluster['start_time']
                end_time = cluster['end_time']
                photo_count = cluster['photo_count']
                
                # Parse centroid from WKT (format: "POINT(lon lat)")
                # Example: "POINT(-122.123 37.456)"
                centroid_parts = centroid_wkt.replace('POINT(', '').replace(')', '').split()
                longitude = float(centroid_parts[0])
                latitude = float(centroid_parts[1])
                
                # Calculate duration
                if start_time == end_time:
                    duration_minutes = 0
                else:
                    duration_minutes = int((end_time - start_time).total_seconds() / 60)
                
                # Extract local date and time from start_time
                local_date = start_time.date()
                local_time = start_time.time()
                
                # Create "photo-session" visit record
                cursor.execute("""
                    INSERT INTO Visits (
                        start_time,
                        end_time,
                        duration_minutes,
                        local_date,
                        local_time,
                        location,
                        location_id,
                        visit_type
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        NULL,
                        'photo_session'
                    )
                    RETURNING id
                """, (start_time, end_time, duration_minutes, local_date, local_time, longitude, latitude))
                
                visit_id = cursor.fetchone()['id']
                visits_created += 1
                
                # Link all photos in cluster to this visit (batch update)
                cursor.execute("""
                    UPDATE Photos 
                    SET visit_id = %s 
                    WHERE id = ANY(%s)
                """, (visit_id, photo_ids))
                photos_linked += len(photo_ids)
                
                # Commit every 100 clusters
                if visits_created % 100 == 0:
                    conn.commit()
                    
            except Exception as e:
                failed_clusters += 1
                conn.rollback()
                if failed_clusters <= 5:  # Only print first few errors
                    print(f"\n  Warning: Failed to process cluster {cluster_id}: {e}")
                continue
        
        # Final commit
        conn.commit()
        
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user (Ctrl-C)")
        print("Committing current progress...")
        conn.commit()
        print(f"✓ Created {visits_created:,} photo-session visits before interruption")
        print(f"✓ Linked {photos_linked:,} photos to visits")
        print(f"✓ Resume by running script again - it will skip already-linked photos")
        cursor.close()
        return visits_created
    
    cursor.close()
    
    print(f"✓ Created {visits_created:,} photo-session visits")
    print(f"✓ Linked {photos_linked:,} photos to new visits")
    if failed_clusters > 0:
        print(f"✗ Failed to process {failed_clusters:,} clusters")
    
    print(f"\nNote: location_id is NULL - run 5_geocode.py to populate")
    
    return visits_created


def print_summary_statistics(conn):
    """Print summary statistics about photo-visit linkage"""
    print("\n" + "="*60)
    print("PHOTO-VISIT LINKAGE SUMMARY")
    print("="*60)
    
    cursor = conn.cursor()
    
    # Total photos
    cursor.execute("SELECT COUNT(*) as count FROM Photos")
    total_photos = cursor.fetchone()['count']
    
    # Photos with GPS
    cursor.execute("""
        SELECT COUNT(*) as count FROM Photos 
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """)
    photos_with_gps = cursor.fetchone()['count']
    
    # Photos with datetime
    cursor.execute("""
        SELECT COUNT(*) as count FROM Photos 
        WHERE capture_datetime IS NOT NULL
    """)
    photos_with_datetime = cursor.fetchone()['count']
    
    # Photos linkable (GPS + datetime)
    cursor.execute("""
        SELECT COUNT(*) as count FROM Photos 
        WHERE latitude IS NOT NULL 
          AND longitude IS NOT NULL
          AND capture_datetime IS NOT NULL
    """)
    photos_linkable = cursor.fetchone()['count']
    
    # Photos linked to visits
    cursor.execute("SELECT COUNT(*) as count FROM Photos WHERE visit_id IS NOT NULL")
    photos_linked = cursor.fetchone()['count']
    
    # Photos linked to timeline visits vs photo-session visits
    cursor.execute("""
        SELECT 
            v.visit_type,
            COUNT(*) AS photo_count
        FROM Photos p
        JOIN Visits v ON p.visit_id = v.id
        GROUP BY v.visit_type
        ORDER BY photo_count DESC
    """)
    visit_type_breakdown = cursor.fetchall()
    
    # Total visits
    cursor.execute("SELECT COUNT(*) as count FROM Visits")
    total_visits = cursor.fetchone()['count']
    
    # Visits by type
    cursor.execute("""
        SELECT visit_type, COUNT(*) as count
        FROM Visits 
        GROUP BY visit_type 
        ORDER BY count DESC
    """)
    visits_by_type = cursor.fetchall()
    
    cursor.close()
    
    print(f"\nPhotos:")
    print(f"  Total photos:           {total_photos:>8,}")
    print(f"  With GPS coordinates:   {photos_with_gps:>8,} ({photos_with_gps/total_photos*100 if total_photos > 0 else 0:.1f}%)")
    print(f"  With datetime:          {photos_with_datetime:>8,} ({photos_with_datetime/total_photos*100 if total_photos > 0 else 0:.1f}%)")
    print(f"  Linkable (GPS+datetime):{photos_linkable:>8,} ({photos_linkable/total_photos*100 if total_photos > 0 else 0:.1f}%)")
    print(f"  Linked to visits:       {photos_linked:>8,} ({photos_linked/photos_linkable*100 if photos_linkable > 0 else 0:.1f}% of linkable)")
    print(f"  Unlinked:               {photos_linkable - photos_linked:>8,}")
    
    if visit_type_breakdown:
        print(f"\nPhotos by Visit Type:")
        for row in visit_type_breakdown:
            print(f"  {row['visit_type'] or 'NULL':<20} {row['photo_count']:>8,}")
    
    print(f"\nVisits:")
    print(f"  Total visits:           {total_visits:>8,}")
    for row in visits_by_type:
        print(f"  {row['visit_type'] or 'NULL':<20} {row['count']:>8,}")
    
    print("="*60 + "\n")


def main():
    """Main execution flow"""
    print("="*60)
    print("PHOTO-VISIT LINKING")
    print("="*60)
    
    # Connect to database
    conn = get_main_connection()
    
    try:
        # Step 1: Match photos to existing visits
        matched_count = match_photos_to_existing_visits(conn)
        
        # Step 2: Cluster unmatched photos
        clusters = cluster_unmatched_photos(conn)
        
        # Step 3: Create photo-session visits for clusters
        visits_created = create_photo_session_visits(conn, clusters)
        
        # Step 4: Print summary statistics
        print_summary_statistics(conn)
        
        print("✓ Photo-visit linking complete!")
        
    except Exception as e:
        print(f"\n✗ Error during photo-visit linking: {e}", file=sys.stderr)
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
