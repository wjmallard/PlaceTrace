"""
Generate a synthetic Google Timeline export for a fictional persona.

Produces demo/location-history.json (plus suggested home/work location
entries) for trying location-history without a real Takeout export, and for
regenerating the README screenshots. Deterministic: same output every run.

The persona lives in San Francisco's Mission District, works downtown,
cycles the Valencia-Market corridor, and takes a few trips: a Monterey day
trip, a Tahoe weekend, a week in New York, and ten days in Paris and
Amsterdam.
"""

import argparse
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

rng = random.Random(42)

SF_TZ_BY_MONTH = {m: -8 if m in (1, 2, 12) else -7 for m in range(1, 13)}

HOME = (37.7599, -122.4213)     # Mission District
WORK = (37.7909, -122.3999)     # Financial District

# Regular haunts around town: (lat, lon, weekly visit probability weight)
SPOTS = [
    (37.7580, -122.4180, 30),   # gym
    (37.7642, -122.4239, 24),   # grocery
    (37.7566, -122.4211, 18),   # coffee
    (37.7596, -122.4269, 12),   # dolores park
    (37.7955, -122.3937, 8),    # ferry building
    (37.7694, -122.4862, 8),    # golden gate park
    (37.7599, -122.5108, 6),    # ocean beach
    (37.8060, -122.4430, 6),    # marina green
    (37.7941, -122.4078, 5),    # chinatown dinner
    (37.8003, -122.4103, 4),    # north beach
    (37.7544, -122.4477, 4),    # twin peaks
    (37.7785, -122.3893, 10),   # soma restaurant
]

# Extra one-off spots scattered around the city
EXTRA_SPOTS = [
    (37.765 + rng.uniform(-0.035, 0.045), -122.43 + rng.uniform(-0.05, 0.045))
    for _ in range(60)
]

TRIPS = [
    # (start day, nights, [(lat, lon)], utc offset hours)
    (date(2024, 6, 8), 0, [(36.6002, -121.8947)], -7),                    # Monterey day trip
    (date(2024, 9, 20), 2, [(39.0968, -120.0324)], -7),                   # Tahoe weekend
    (date(2025, 4, 12), 6, [(40.7359, -73.9911), (40.7794, -73.9632)], -4),   # New York week
    (date(2025, 8, 2), 9, [(48.8606, 2.3376), (52.3676, 4.9041)], 2),     # Paris + Amsterdam
]


def iso(dt, offset_hours):
    tz = timezone(timedelta(hours=offset_hours))
    return dt.replace(tzinfo=tz).isoformat(timespec='seconds')


def geo(lat, lon):
    """Jittered coordinate, for activity endpoints."""
    jitter = 0.00015
    return f"geo:{lat + rng.uniform(-jitter, jitter):.6f},{lon + rng.uniform(-jitter, jitter):.6f}"


def visit(day, start_h, end_h, lat, lon, offset, semantic=None, place_id=None):
    start = datetime.combine(day, time(0)) + timedelta(hours=start_h)
    end = datetime.combine(day, time(0)) + timedelta(hours=end_h)
    top = {
        # Exact coordinates: visits snap to the place, like real Timeline data
        'placeLocation': f"geo:{lat:.6f},{lon:.6f}",
        'placeID': place_id or f"demo{abs(hash((round(lat, 3), round(lon, 3)))) % 10**10}",
    }
    if semantic:
        top['semanticType'] = semantic
    return {
        'startTime': iso(start, offset),
        'endTime': iso(end, offset),
        'visit': {'topCandidate': top},
    }


def activity(day, start_h, end_h, from_pt, to_pt, kind, offset, km):
    start = datetime.combine(day, time(0)) + timedelta(hours=start_h)
    end = datetime.combine(day, time(0)) + timedelta(hours=end_h)
    return {
        'startTime': iso(start, offset),
        'endTime': iso(end, offset),
        'activity': {
            'start': geo(*from_pt),
            'end': geo(*to_pt),
            'topCandidate': {'type': kind, 'probability': f"{rng.uniform(0.7, 0.98):.2f}"},
            'distanceMeters': f"{km * 1000:.1f}",
        },
    }


def segment(day, start_h, end_h, waypoints, kind, offset, km):
    """New-format activitySegment with a waypoint route (renders colored)."""
    start = datetime.combine(day, time(0)) + timedelta(hours=start_h)
    end = datetime.combine(day, time(0)) + timedelta(hours=end_h)
    return {
        'startTime': iso(start, offset),
        'endTime': iso(end, offset),
        'activitySegment': {
            'start': f"geo:{waypoints[0][0]:.6f},{waypoints[0][1]:.6f}",
            'end': f"geo:{waypoints[-1][0]:.6f},{waypoints[-1][1]:.6f}",
            'topCandidate': {'type': kind, 'probability': f"{rng.uniform(0.8, 0.98):.2f}"},
            'distanceMeters': f"{km * 1000:.1f}",
            'timelinePath': {
                'waypoints': [
                    {'latE7': round(lat * 1e7), 'lngE7': round(lon * 1e7)}
                    for lat, lon in waypoints
                ],
            },
        },
    }


def commute_path(homebound=False):
    """A plausible ride up Valencia and along the Market Street diagonal."""
    base = [
        (37.7599, -122.4213), (37.7639, -122.4218), (37.7679, -122.4222),
        (37.7712, -122.4225), (37.7752, -122.4190), (37.7789, -122.4137),
        (37.7822, -122.4090), (37.7855, -122.4048), (37.7887, -122.4014),
        (37.7909, -122.3999),
    ]
    pts = [(lat + rng.uniform(-0.0004, 0.0004), lon + rng.uniform(-0.0004, 0.0004)) for lat, lon in base]
    return pts[::-1] if homebound else pts


def weekday_entries(day, offset):
    entries = []
    wake = 8 + rng.uniform(-0.5, 0.5)
    entries.append(visit(day, 0.1, wake, *HOME, offset, semantic='Home'))
    commute = 0.55 + rng.uniform(-0.1, 0.1)
    entries.append(segment(day, wake, wake + commute, commute_path(), 'cycling', offset, 4.6))
    work_end = 17.5 + rng.uniform(-1, 1)
    entries.append(visit(day, wake + commute, work_end, *WORK, offset, semantic='Work', place_id='demoWORKPLACE01'))
    entries.append(segment(day, work_end, work_end + commute, commute_path(homebound=True), 'cycling', offset, 4.6))

    evening = work_end + commute
    if rng.random() < 0.55:
        lat, lon, _ = rng.choices(SPOTS, weights=[w for _, _, w in SPOTS])[0][0:3]
        dinner_end = evening + rng.uniform(0.8, 2.2)
        entries.append(activity(day, evening, evening + 0.2, HOME, (lat, lon), 'walking', offset, 1.1))
        entries.append(visit(day, evening + 0.2, dinner_end, lat, lon, offset))
        entries.append(activity(day, dinner_end, dinner_end + 0.2, (lat, lon), HOME, 'walking', offset, 1.1))
        evening = dinner_end + 0.2
    entries.append(visit(day, evening, 23.9, *HOME, offset, semantic='Home'))
    return entries


def weekend_entries(day, offset):
    entries = []
    entries.append(visit(day, 0.1, 10 + rng.uniform(-1, 1), *HOME, offset, semantic='Home'))
    t = 10.5
    outings = rng.randint(1, 3)
    for _ in range(outings):
        if rng.random() < 0.3 and EXTRA_SPOTS:
            lat, lon = rng.choice(EXTRA_SPOTS)
        else:
            lat, lon, _ = rng.choices(SPOTS, weights=[w for _, _, w in SPOTS])[0][0:3]
        end = t + rng.uniform(1, 3)
        entries.append(activity(day, t - 0.25, t, HOME, (lat, lon), rng.choice(['walking', 'cycling', 'in passenger vehicle']), offset, rng.uniform(1, 6)))
        entries.append(visit(day, t, end, lat, lon, offset))
        t = end + 0.4
    entries.append(visit(day, t, 23.9, *HOME, offset, semantic='Home'))
    return entries


def trip_entries(start_day, nights, stops, trip_offset, home_offset):
    entries = []
    # Departure day: morning at home, drive/fly out, evening at destination
    entries.append(visit(start_day, 0.1, 8.5, *HOME, home_offset, semantic='Home'))
    lat, lon = stops[0]
    entries.append(activity(start_day, 9, 12, HOME, (lat, lon), 'in passenger vehicle', home_offset, 150 if nights < 3 else 1100))
    day = start_day
    if nights == 0:
        entries.append(visit(day, 12.2, 19.5, lat, lon, trip_offset))
        entries.append(activity(day, 19.7, 22.4, (lat, lon), HOME, 'in passenger vehicle', trip_offset, 150))
        entries.append(visit(day, 22.6, 23.9, *HOME, home_offset, semantic='Home'))
        return entries

    entries.append(visit(day, 13, 23.9, lat, lon, trip_offset))
    for night in range(1, nights + 1):
        day = start_day + timedelta(days=night)
        lat, lon = stops[min(night * len(stops) // (nights + 1), len(stops) - 1)]
        entries.append(visit(day, 0.1, 9.5, lat, lon, trip_offset))
        for lat2, lon2 in [(lat + rng.uniform(-0.02, 0.02), lon + rng.uniform(-0.02, 0.02)) for _ in range(2)]:
            t = 10 + rng.uniform(0, 6)
            entries.append(visit(day, t, t + rng.uniform(1, 2.5), lat2, lon2, trip_offset))
        entries.append(visit(day, 20, 23.9, lat, lon, trip_offset))
    # Return day
    day = start_day + timedelta(days=nights + 1)
    entries.append(visit(day, 0.1, 10, lat, lon, trip_offset))
    entries.append(activity(day, 10.5, 15.5, (lat, lon), HOME, 'in passenger vehicle', trip_offset, 150 if nights < 3 else 1100))
    entries.append(visit(day, 16, 23.9, *HOME, home_offset, semantic='Home'))
    return entries


def main():
    parser = argparse.ArgumentParser(description="Generate a synthetic Timeline export for demos.")
    parser.add_argument(
        "--out",
        default="demo/location-history.json",
        help="output path (default: demo/location-history.json)",
    )
    args = parser.parse_args()

    trip_days = set()
    for start, nights, _, _ in TRIPS:
        for d in range((start - date(2024, 1, 1)).days, (start - date(2024, 1, 1)).days + nights + 2):
            trip_days.add(d)

    entries = []
    day = date(2024, 1, 1)
    while day <= date(2025, 12, 31):
        offset = SF_TZ_BY_MONTH[day.month]
        day_index = (day - date(2024, 1, 1)).days
        if day_index not in trip_days:
            if day.weekday() < 5:
                entries.extend(weekday_entries(day, offset))
            else:
                entries.extend(weekend_entries(day, offset))
        day += timedelta(days=1)

    for start, nights, stops, trip_offset in TRIPS:
        home_offset = SF_TZ_BY_MONTH[start.month]
        entries.extend(trip_entries(start, nights, stops, trip_offset, home_offset))

    entries.sort(key=lambda e: e['startTime'])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump(entries, f)

    print(f"wrote {len(entries):,} timeline entries to {out}")
    print()
    print("home_locations.json entry:")
    print(json.dumps([{
        'lat': HOME[0], 'lon': HOME[1], 'place_id': None,
        'name': 'Demo Home', 'start_date': None, 'end_date': None,
    }], indent=2))
    print()
    print("work_locations.json entry:")
    print(json.dumps([{
        'lat': WORK[0], 'lon': WORK[1], 'place_id': 'demoWORKPLACE01',
        'name': 'Demo Office', 'start_date': None, 'end_date': None,
    }], indent=2))


if __name__ == '__main__':
    main()
