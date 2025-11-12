#!/bin/bash
echo Run this:
echo dropdb unified_location_photos
echo
echo To reset the photos table:
echo psql unified_location_photos -c \"TRUNCATE TABLE Photos CASCADE\;\"
