# PlaceTrace

Personal location history explorer built on PostGIS. Imports Google Timeline data, reverse-geocodes against a local OSM database, detects trips, and serves an interactive web UI for browsing your location history.

## Features

- **Ingestion pipeline** — imports visits, movements, and activity data from Google Takeout JSON, with resume support and progress tracking
- **Local reverse geocoding** — batch geocodes coordinates against a local OSM boundaries database using PostGIS spatial queries, no external API calls
- **Trip detection** — identifies day trips, short trips, and long trips from visit patterns using configurable distance/time thresholds and movement data to bridge gaps
- **Interactive web UI** — Leaflet map with clustered markers, spatial and temporal filtering, trip explorer with year grouping, and a sortable visit table
- **CLI query tools** — search visits by location, explore trips, view yearly summaries, and query top destinations from the terminal

## Stack

- **Database:** PostgreSQL + PostGIS
- **Backend:** Flask, SQLAlchemy, GeoAlchemy2
- **Frontend:** Leaflet, Alpine.js, Tailwind CSS
- **Pipeline:** Python multiprocessing with psycopg (raw SQL for bulk operations)

## Data Source

PlaceTrace works with Google Timeline data exported via [Google Takeout](https://takeout.google.com/). Select **Location History** and export in JSON format. The export produces a `location-history.json` file containing your visits and movements.

## OSM Boundaries Database

The geocoder requires a local OSM boundaries database. Prerequisites: [osmium-tool](https://osmcode.org/osmium-tool/) and [osm2pgsql](https://osm2pgsql.org/) (v2.2+).

Download the planet file (~85 GB) and filter to admin boundaries (~1.3 GB):

```bash
curl -LO https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf
osmium tags-filter planet-latest.osm.pbf boundary=administrative -o boundaries.osm.pbf
```

Create the database and import:

```bash
createdb osm_boundaries
psql osm_boundaries -c "CREATE EXTENSION IF NOT EXISTS postgis;"
osm2pgsql -d osm_boundaries -O flex -S sql/boundaries.lua boundaries.osm.pbf
```

This imports ~300k admin boundaries (countries, states, counties, cities) and takes about 5 minutes.

## Installation

Copy the default config file:

```bash
cp config.yaml.example config.yaml
```

Point `config.yaml` to your `location-history.json` from Google Takeout.

Install Postgres and configure the database:

```bash
createdb placetrace
psql placetrace < sql/schema.sql
```

Import your data and launch the web app:

```bash
uv sync
uv run pt-ingest
uv run pt-web
```

Run `uv run pt` to see all available commands.
