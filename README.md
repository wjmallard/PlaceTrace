# PlaceTrace

Personal location history explorer. Imports Google Timeline data,
reverse-geocodes locations, detects trips, and serves a web UI for browsing.

## Installation

Copy the default config file:

```bash
cp config.yaml.example config.yaml
```

Point `config.yaml` to your `location-history.json` from Google Takeout.

Install Postgres and configure the database.

```bash
createdb placetrace
psql placetrace < sql/schema.sql
```

Import your data and launch the web app.

```bash
uv sync
uv run pt-ingest
uv run pt-web
```

Run `uv run pt` to see all available commands.
