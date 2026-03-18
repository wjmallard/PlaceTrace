# Pipeline

Run all steps at once with `pt-ingest`, or resume from a step with `pt-ingest --from <step>`.

Individual steps, in order:

1. `pt-import-visits` — Import visits from Google Timeline JSON
2. `pt-import-movements` — Import movements/activities from Google Timeline JSON
3. `pt-geocode` — Batch reverse-geocode visits
4. `pt-detect-trips` — Detect and categorize trips from visit history
