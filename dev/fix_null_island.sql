-- Clean up (0, 0) "Null Island" coordinates
-- These are placeholders for missing GPS data and should be NULL

BEGIN;

-- Fix Photos with (0, 0) coordinates
UPDATE Photos 
SET latitude = NULL, 
    longitude = NULL,
    location_id = NULL
WHERE latitude = 0 
  AND longitude = 0;

-- Show summary
SELECT 
    COUNT(*) as total_photos,
    COUNT(latitude) as photos_with_gps,
    COUNT(location_id) as photos_geocoded
FROM Photos;

COMMIT;

