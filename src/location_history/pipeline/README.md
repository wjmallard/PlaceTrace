# Pipeline

Run all steps at once with `lh-ingest`, or resume from a step with `lh-ingest --from <step>`.

Individual steps, in order:

1. `lh-import-visits` — Import visits from Google Timeline JSON
2. `lh-import-movements` — Import movements/activities from Google Timeline JSON
3. `lh-geocode` — Batch reverse-geocode visits
4. `lh-detect-trips` — Detect and categorize trips from visit history

Optional (not part of `lh-ingest`):

- `lh-import-arc` — Import Arc app daily exports: movements with dense routes, plus hand-corrected place names applied to overlapping visits
