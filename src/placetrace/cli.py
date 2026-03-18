"""PlaceTrace CLI — list available commands."""

import sys


def main():
    verbose = "--all" in sys.argv

    print("PlaceTrace — available commands:\n")
    print(f"  {"pt-web":<25} Start the web UI")
    print(f"  {"pt-ingest":<25} Run the full ingest pipeline")
    print(f"  {"pt-query":<25} Query location history from the CLI")

    print(f"\nConfiguration:\n")
    print(f"  {"pt-find-places":<25} Interactive home/work location finder")
    print(f"  {"pt-manage-places":<25} Auto-detect and manage home/work locations")

    if verbose:
        print(f"\nPipeline steps (also available individually):\n")
        print(f"  {"pt-import-visits":<25} Import visits from Google Timeline JSON")
        print(f"  {"pt-import-movements":<25} Import movements from Google Timeline JSON")
        print(f"  {"pt-import-photos":<25} Import photos from Google Takeout directories")
        print(f"  {"pt-link-photos":<25} Link photos to visits via spatio-temporal matching")
        print(f"  {"pt-geocode":<25} Batch reverse-geocode visits and photos")
        print(f"  {"pt-detect-trips":<25} Detect and categorize trips from visit history")
        print(f"  {"pt-generate-thumbnails":<25} Generate photo thumbnails for the web UI")
    else:
        print(f"\nRun pt --all to see individual pipeline steps.")

    print()


if __name__ == "__main__":
    main()
