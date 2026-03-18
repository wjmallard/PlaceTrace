"""PlaceTrace CLI — list available commands."""

import sys


COMMANDS = {
    "pt-web": "Start the web UI",
    "pt-import-visits": "Import visits from Google Timeline JSON",
    "pt-import-movements": "Import movements from Google Timeline JSON",
    "pt-import-photos": "Import photos from Google Takeout directories",
    "pt-link-photos": "Link photos to visits via spatio-temporal matching",
    "pt-geocode": "Batch reverse-geocode visits and photos",
    "pt-detect-trips": "Detect and categorize trips from visit history",
    "pt-generate-thumbnails": "Generate photo thumbnails for the web UI",
    "pt-places-find": "Interactive home/work location finder",
    "pt-places-manage": "Auto-detect and manage home/work locations",
    "pt-places-query": "Query location history from the command line",
}


def main():
    print("PlaceTrace — available commands:\n")
    for cmd, desc in COMMANDS.items():
        print(f"  {cmd:<25} {desc}")
    print()
    print("Run any command with --help for more info.")
    print("Example: uv run pt-web")


if __name__ == "__main__":
    main()
