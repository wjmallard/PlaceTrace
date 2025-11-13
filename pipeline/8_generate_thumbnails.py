#!/usr/bin/env python3
"""
Generate thumbnails for all photos in the database.
This should be run after photo import to pre-generate thumbnails for the web interface.
"""

from PIL import Image, ImageFile
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor

# Import database module
from db import get_main_connection, config

# Allow loading of truncated images (corrupted files)
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Register HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORT = True
except ImportError:
    HEIC_SUPPORT = False
    print("WARNING: pillow-heif not installed. HEIC files will fail.")
    print("Install with: pip install pillow-heif")

# Thumbnail settings
THUMBNAIL_DIR = Path(config['source_data']['thumbnails_directory'])
THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAIL_SIZE = (250, 250)
NUM_WORKERS = config['processing']['num_workers']

def generate_thumbnail_worker(photo_tuple):
    """
    Generate thumbnail for a single photo (worker function for multiprocessing).
    
    Args:
        photo_tuple: (photo_id, file_path)
    
    Returns:
        Tuple of (photo_id, thumbnail_path, success, error_info)
    """
    photo_id, file_path = photo_tuple
    
    try:
        # Open original image
        img = Image.open(file_path)
        
        # Convert to RGB if necessary (handles RGBA, grayscale, etc.)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        
        # Create thumbnail (maintains aspect ratio)
        img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        
        # Save thumbnail to disk with absolute path
        thumbnail_filename = f"thumb_{photo_id}.jpg"
        thumbnail_path = (THUMBNAIL_DIR / thumbnail_filename).resolve()
        img.save(thumbnail_path, "JPEG", quality=85)
        
        return (photo_id, str(thumbnail_path), True, None)
    
    except Exception as e:
        # Get file extension for error tracking
        file_ext = Path(file_path).suffix.lower()
        error_info = {
            'file_path': file_path,
            'extension': file_ext,
            'error': str(e),
            'error_type': type(e).__name__
        }
        return (photo_id, None, False, error_info)

def main():
    print("=" * 80)
    print("THUMBNAIL GENERATION")
    print("=" * 80)
    
    conn = get_main_connection()
    
    # Get all photos without thumbnails
    print("\nQuerying photos without thumbnails...")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, file_path 
        FROM photos 
        WHERE thumbnail_path IS NULL 
          AND file_path IS NOT NULL
        ORDER BY id
    """)
    photos = cursor.fetchall()
    cursor.close()
    
    total_photos = len(photos)
    
    if total_photos == 0:
        print("✓ All photos already have thumbnails!")
        conn.close()
        return
    
    print(f"\nFound {total_photos:,} photos needing thumbnails")
    print(f"Thumbnail size: {THUMBNAIL_SIZE[0]}x{THUMBNAIL_SIZE[1]}")
    print(f"Output directory: {THUMBNAIL_DIR.resolve()}")
    print(f"Workers: {NUM_WORKERS}")
    print(f"HEIC support: {'✓ Enabled' if HEIC_SUPPORT else '✗ Disabled (install pillow-heif)'}")
    print()
    
    # Prepare work items (photo_id, file_path)
    work_items = [
        (photo['id'], photo['file_path'])
        for photo in photos
    ]
    
    # Generate thumbnails with parallel workers
    success_count = 0
    failed_count = 0
    failed_ids = []
    error_by_extension = {}
    error_by_type = {}
    error_details = []
    
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        results = list(tqdm(
            executor.map(generate_thumbnail_worker, work_items),
            total=len(work_items),
            desc="Generating thumbnails",
            unit="photo"
        ))
    
    # Update database with results
    print("\nUpdating database...")
    cursor = conn.cursor()
    for photo_id, thumbnail_path, success, error_info in tqdm(results, desc="Writing to DB", unit="photo"):
        if success:
            cursor.execute(
                "UPDATE photos SET thumbnail_path = %s WHERE id = %s",
                (thumbnail_path, photo_id)
            )
            success_count += 1
        else:
            failed_count += 1
            failed_ids.append(photo_id)
            
            # Track error statistics
            if error_info:
                ext = error_info['extension']
                error_type = error_info['error_type']
                
                error_by_extension[ext] = error_by_extension.get(ext, 0) + 1
                error_by_type[error_type] = error_by_type.get(error_type, 0) + 1
                error_details.append(error_info)
    
    conn.commit()
    cursor.close()
    conn.close()
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total photos:        {total_photos:,}")
    print(f"Thumbnails created:  {success_count:,}")
    print(f"Failed:              {failed_count:,}")
    
    if failed_count > 0:
        print(f"\n⚠️  {failed_count} photos failed thumbnail generation")
        print(f"\nFailed photo IDs: {failed_ids[:10]}" + (" ..." if len(failed_ids) > 10 else ""))
        
        # Show error breakdown by file extension
        print("\nFailures by file extension:")
        for ext, count in sorted(error_by_extension.items(), key=lambda x: -x[1]):
            print(f"  {ext or '(no extension)'}: {count}")
        
        # Show error breakdown by error type
        print("\nFailures by error type:")
        for error_type, count in sorted(error_by_type.items(), key=lambda x: -x[1]):
            print(f"  {error_type}: {count}")
        
        # Show a few example errors
        print("\nExample errors:")
        for error_info in error_details[:5]:
            print(f"  File: {Path(error_info['file_path']).name}")
            print(f"    Extension: {error_info['extension']}")
            print(f"    Error: {error_info['error_type']}: {error_info['error']}")
    else:
        print("\n✓ All thumbnails generated successfully!")

if __name__ == '__main__':
    main()
