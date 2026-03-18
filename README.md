# PlaceTrace

Personal location history explorer. Imports Google Timeline data and photos,
reverse-geocodes locations, detects trips, and serves a web UI for browsing.

## Quick Start

```bash
uv sync
cp config.yaml.example config.yaml   # edit with your paths
./scripts/initialize_database.sh
uv run pt-ingest
uv run pt-web
```

## Commands

Run `uv run pt` to see all available commands.
