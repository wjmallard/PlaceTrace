#!/bin/sh
psql placetrace -c "TRUNCATE Trips CASCADE;"
