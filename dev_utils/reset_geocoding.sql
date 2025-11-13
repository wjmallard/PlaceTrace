-- Reset Geocoding Data

BEGIN;

-- Clear location_id from Photos
UPDATE Photos 
SET location_id = NULL 
WHERE location_id IS NOT NULL;

-- Clear location_id from Visits
UPDATE Visits 
SET location_id = NULL 
WHERE location_id IS NOT NULL;

-- Delete all locations
DELETE FROM Locations;

-- Reset the Locations sequence
ALTER SEQUENCE locations_id_seq RESTART WITH 1;

COMMIT;

-- Show summary
SELECT 
    (SELECT COUNT(*) FROM Photos WHERE location_id IS NULL) as photos_to_geocode,
    (SELECT COUNT(*) FROM Visits WHERE location_id IS NULL) as visits_to_geocode,
    (SELECT COUNT(*) FROM Locations) as total_locations;
