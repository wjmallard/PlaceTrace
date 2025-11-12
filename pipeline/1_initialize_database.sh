#!/bin/sh

createdb unified_location_photos
psql unified_location_photos < schema.sql

