# Pipeline

Run all steps at once with `pt-ingest`, or resume from a step with `pt-ingest --from <step>`.

Individual steps, in order:

1. `pt-import-visits` — Import visits from Google Timeline JSON
2. `pt-import-movements` — Import movements/activities from Google Timeline JSON
3. `pt-geocode` — Batch reverse-geocode visits
4. `pt-detect-trips` — Detect and categorize trips from visit history

Optional (not part of `pt-ingest`):

- `pt-import-arc` — Import Arc app daily exports: movements with dense routes, plus hand-corrected place names applied to overlapping visits
