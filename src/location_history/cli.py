"""location-history CLI — list available commands."""

import argparse


def main():
    parser = argparse.ArgumentParser(description="List available location-history commands.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="also show individual pipeline steps",
    )
    args = parser.parse_args()

    print("location-history — available commands:\n")
    print(f"  {"lh-web":<25} Start the web UI")
    print(f"  {"lh-ingest":<25} Run the full ingest pipeline")
    print(f"  {"lh-query":<25} Query location history from the CLI")

    print("\nConfiguration:\n")
    print(f"  {"lh-find-places":<25} Interactive home/work location finder")
    print(f"  {"lh-manage-places":<25} Auto-detect and manage home/work locations")

    if args.all:
        print("\nPipeline steps (also available individually):\n")
        print(f"  {"lh-import-visits":<25} Import visits from Google Timeline JSON")
        print(f"  {"lh-import-movements":<25} Import movements from Google Timeline JSON")
        print(f"  {"lh-import-arc":<25} Import Arc app exports (movements + place names)")
        print(f"  {"lh-geocode":<25} Batch reverse-geocode visits")
        print(f"  {"lh-detect-trips":<25} Detect and categorize trips from visit history")
    else:
        print("\nRun lh --all to see individual pipeline steps.")

    print()


if __name__ == "__main__":
    main()
