"""
lh-ingest — Run the full location-history pipeline in order.

Usage:
    lh-ingest                  # run all steps
    lh-ingest --from geocode   # resume from a specific step
    lh-ingest --list           # show available steps
"""

import argparse
import importlib
import sys

STEPS = [
    ("import-visits",    "location_history.pipeline.import_visits"),
    ("import-movements", "location_history.pipeline.import_movements"),
    ("geocode",          "location_history.pipeline.geocode"),
    ("detect-trips",     "location_history.pipeline.detect_trips"),
]

STEP_NAMES = [name for name, _ in STEPS]


def main():
    parser = argparse.ArgumentParser(
        description="Run the full location-history pipeline in order.",
    )
    parser.add_argument(
        "--from",
        dest="start_from",
        choices=STEP_NAMES,
        help="resume from this step",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="show available steps and exit",
    )
    args = parser.parse_args()

    if args.list:
        print("Pipeline steps (in order):\n")
        for i, (name, _) in enumerate(STEPS, 1):
            print(f"  {i}. {name}")
        return

    # Determine which steps to run
    if args.start_from:
        steps_to_run = STEPS[STEP_NAMES.index(args.start_from):]
        print(f"Resuming pipeline from '{args.start_from}' ({len(steps_to_run)} steps)\n")
    else:
        steps_to_run = STEPS
        print(f"Running full pipeline ({len(steps_to_run)} steps)\n")

    for i, (name, module_path) in enumerate(steps_to_run, 1):
        print(f"\n{'='*60}")
        print(f"Step {i}/{len(steps_to_run)}: {name}")
        print(f"{'='*60}\n")

        try:
            module = importlib.import_module(module_path)
            module.main(argv=[])
        except KeyboardInterrupt:
            print(f"\n\nInterrupted during '{name}'. Resume with:")
            print(f"  lh-ingest --from {name}")
            sys.exit(1)
        except Exception as e:
            print(f"\nError during '{name}': {e}")
            print(f"Fix the issue and resume with:")
            print(f"  lh-ingest --from {name}")
            sys.exit(1)

    print(f"\n{'='*60}")
    print("Pipeline complete!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
