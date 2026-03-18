# Pipeline

Run in order:

1. `pt-import-visits` — Import visits from Google Timeline JSON
2. `pt-import-movements` — Import movements/activities from Google Timeline JSON
3. `pt-import-photos` — Import photos from Google Takeout directories
4. `pt-link-photos` — Link photos to visits via spatio-temporal matching
5. `pt-geocode` — Batch reverse-geocode visits and photos
6. `pt-detect-trips` — Detect and categorize trips from visit history
7. `pt-generate-thumbnails` — Generate photo thumbnails for the web UI
