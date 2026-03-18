#!/usr/bin/env python3
"""
pt-ingest — Run the full PlaceTrace pipeline in order.

Usage:
    pt-ingest                  # run all steps
    pt-ingest --from geocode   # resume from a specific step
    pt-ingest --list           # show available steps
"""

import sys

STEPS = [
    ("import-visits",    "placetrace.pipeline.import_visits"),
    ("import-movements", "placetrace.pipeline.import_movements"),
    ("import-photos",    "placetrace.pipeline.import_photos"),
    ("link-photos",      "placetrace.pipeline.link_photos"),
    ("geocode",          "placetrace.pipeline.geocode"),
    ("detect-trips",     "placetrace.pipeline.detect_trips"),
]

STEP_NAMES = [name for name, _ in STEPS]


def main():
    args = sys.argv[1:]

    if "--list" in args:
        print("Pipeline steps (in order):\n")
        for i, (name, _) in enumerate(STEPS, 1):
            print(f"  {i}. {name}")
        return

    start_from = None
    if "--from" in args:
        idx = args.index("--from")
        if idx + 1 >= len(args):
            print("Error: --from requires a step name")
            print(f"Available steps: {', '.join(STEP_NAMES)}")
            sys.exit(1)
        start_from = args[idx + 1]
        if start_from not in STEP_NAMES:
            print(f"Error: unknown step '{start_from}'")
            print(f"Available steps: {', '.join(STEP_NAMES)}")
            sys.exit(1)

    # Determine which steps to run
    if start_from:
        start_idx = STEP_NAMES.index(start_from)
        steps_to_run = STEPS[start_idx:]
        print(f"Resuming pipeline from '{start_from}' ({len(steps_to_run)} steps)\n")
    else:
        steps_to_run = STEPS
        print(f"Running full pipeline ({len(steps_to_run)} steps)\n")

    for i, (name, module_path) in enumerate(steps_to_run, 1):
        print(f"\n{'='*60}")
        print(f"Step {i}/{len(steps_to_run)}: {name}")
        print(f"{'='*60}\n")

        try:
            module = __import__(module_path, fromlist=["main"])
            module.main()
        except KeyboardInterrupt:
            print(f"\n\nInterrupted during '{name}'. Resume with:")
            print(f"  pt-ingest --from {name}")
            sys.exit(1)
        except Exception as e:
            print(f"\nError during '{name}': {e}")
            print(f"Fix the issue and resume with:")
            print(f"  pt-ingest --from {name}")
            sys.exit(1)

    print(f"\n{'='*60}")
    print("Pipeline complete!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
