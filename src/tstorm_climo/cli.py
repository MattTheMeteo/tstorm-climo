"""Command-line interface for tstorm-climo."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from tstorm_climo.artifacts import build_hourly_observation_artifact
from tstorm_climo.catalog import KIND_MATCHES, fetch_places, save_places
from tstorm_climo.climatology import summarize_grid
from tstorm_climo.extract import extract_series
from tstorm_climo.hrrr import load_precipitation
from tstorm_climo.osm import KIND_TAGS, build_query, run_query
from tstorm_climo.places import deduplicate, from_overpass, load_csv, save_csv
from tstorm_climo.provenance import (
    ARCHIVE_ATTRIBUTION,
    PLACES_ATTRIBUTION,
    build_metadata,
    format_caveats,
    write_metadata,
)
from tstorm_climo.summarize import (
    DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
    by_local_month,
    place_summary,
    scorecard,
    split_half_reliability,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="tstorm-climo",
        description=("Build a precipitation climatology from HRRR analysis data."),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize = subparsers.add_parser(
        "summarize",
        help="Summarize precipitation events over a time range.",
    )
    summarize.add_argument(
        "--start",
        required=True,
        help="Inclusive start of the time range (e.g. 2025-07-01).",
    )
    summarize.add_argument(
        "--end",
        required=True,
        help="Inclusive end of the time range (e.g. 2025-07-31).",
    )

    build = subparsers.add_parser(
        "build-observations",
        help="Build a reusable hourly rain-observation artifact.",
    )
    build.add_argument(
        "--places",
        required=True,
        help="Places CSV, as written by fetch-places.",
    )
    build.add_argument(
        "--start",
        required=True,
        help="Inclusive start of the time range (e.g. 2018-01-01).",
    )
    build.add_argument(
        "--end",
        required=True,
        help="Inclusive end of the time range (e.g. 2025-12-31).",
    )
    build.add_argument(
        "--output",
        required=True,
        help="New output directory for the Parquet files and manifest.",
    )

    report = subparsers.add_parser(
        "rain-report",
        help="Rank places by how often it rains on them.",
    )
    report.add_argument(
        "--places",
        required=True,
        help="Places CSV, as written by fetch-places.",
    )
    report.add_argument(
        "--start",
        required=True,
        help="Inclusive start of the time range (e.g. 2025-06-01).",
    )
    report.add_argument(
        "--end",
        required=True,
        help="Inclusive end of the time range (e.g. 2025-07-01).",
    )
    report.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
        help=(
            "Wet threshold in mm/h "
            f"(default: {DEFAULT_WET_THRESHOLD_MM_PER_HOUR}). Higher "
            "values rank places more reliably; see the reliability "
            "figure printed with the table."
        ),
    )
    report.add_argument(
        "--kind",
        action="append",
        help=(
            "Only report this place kind (e.g. golf_course). "
            "Repeatable; omit for all kinds."
        ),
    )
    report.add_argument(
        "--month",
        type=int,
        choices=range(1, 13),
        help="Restrict to one calendar month, in local time (1-12).",
    )
    report.add_argument(
        "--output",
        help=(
            "Write the table to this path as parquet (or .csv by "
            "extension) with a .meta.json provenance sidecar."
        ),
    )

    catalog = subparsers.add_parser(
        "build-catalog",
        help="Pull a place catalog from the LayerCake OSM POI parquet.",
    )
    catalog.add_argument(
        "--kind",
        action="append",
        required=True,
        choices=sorted(KIND_MATCHES),
        help="Place kind to pull; repeatable. Required.",
    )
    catalog.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("SOUTH", "WEST", "NORTH", "EAST"),
        help=(
            "Restrict to this bounding box in decimal degrees. Omit for "
            "the whole (worldwide) source."
        ),
    )
    catalog.add_argument(
        "--output",
        required=True,
        help=(
            "Directory to create for this release. Holds places.parquet "
            "and manifest.json. Must not already exist."
        ),
    )

    fetch = subparsers.add_parser(
        "fetch-places",
        help="Fetch named places from OpenStreetMap via Overpass.",
    )
    fetch.add_argument(
        "--bbox",
        required=True,
        nargs=4,
        type=float,
        metavar=("SOUTH", "WEST", "NORTH", "EAST"),
        help="Bounding box in decimal degrees.",
    )
    fetch.add_argument(
        "--kind",
        action="append",
        choices=sorted(KIND_TAGS),
        help="Place kind to fetch; repeatable.",
    )
    fetch.add_argument(
        "--named-only",
        action="store_true",
        help="Drop places without a name tag.",
    )
    fetch.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Skip near-duplicate removal.",
    )
    fetch.add_argument(
        "--output",
        required=True,
        help="CSV path to write the places to.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the tstorm-climo console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "summarize":
        precipitation = load_precipitation(args.start, args.end)
        events = summarize_grid(precipitation)
        for time, count in events.items():
            print(f"{time}: {count} grid points with precipitation")
        return 0

    if args.command == "build-observations":
        return _build_observations(args)

    if args.command == "rain-report":
        return _rain_report(args)

    if args.command == "build-catalog":
        bbox = tuple(args.bbox) if args.bbox else None
        places = fetch_places(args.kind, bbox=bbox)
        if places.height == 0:
            print("No places matched.", file=sys.stderr)
            return 1
        release = save_places(
            places, args.output, kinds=args.kind
        )
        counts = places.group_by("kind").len().sort("kind")
        for row in counts.iter_rows():
            print(f"  {row[0]}: {row[1]}")
        print(f"Wrote {places.height} places to {release}")
        print(f"Places: {PLACES_ATTRIBUTION}")
        return 0

    if args.command == "fetch-places":
        kinds = args.kind or list(KIND_TAGS)
        query = build_query(tuple(args.bbox), kinds)
        elements = run_query(query)
        places = [
            place
            for element in elements
            if (place := from_overpass(kinds, element)) is not None
        ]
        if args.named_only:
            places = [p for p in places if p.name]
        if not args.no_dedupe:
            places = deduplicate(places)
        save_csv(places, args.output)
        print(f"Wrote {len(places)} places to {args.output}")
        return 0

    return 1


def _build_observations(args: argparse.Namespace) -> int:
    """Build a versioned hourly observation artifact from a places CSV."""
    places = load_csv(args.places)
    if not places:
        print("No places to build observations for.", file=sys.stderr)
        return 1
    try:
        manifest = build_hourly_observation_artifact(
            places, args.start, args.end, args.output
        )
    except (FileExistsError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(
        f"Wrote {manifest['observations']['row_count']} observations for "
        f"{manifest['place_catalog']['place_count']} places to {args.output}"
    )
    print(
        f"Observed {manifest['date_range']['observed_start']} through "
        f"{manifest['date_range']['observed_end']}"
    )
    print(f"Valid observation rows: {manifest['observations']['valid_fraction']:.1%}")
    return 0


def _rain_report(args: argparse.Namespace) -> int:
    """Rank places by rain frequency and print the Chuck table."""
    places = load_csv(args.places)
    if args.kind:
        wanted = set(args.kind)
        places = [p for p in places if p.kind in wanted]
    if not places:
        print("No places to report on.", file=sys.stderr)
        return 1

    series = extract_series(places, args.start, args.end)
    if series.height == 0:
        print(
            f"No data for {args.start} to {args.end}. The archive's usable "
            "window starts 2018-01-01.",
            file=sys.stderr,
        )
        return 1

    table = (
        by_local_month(series, places, threshold_mm_per_hour=args.threshold)
        if args.month is not None
        else place_summary(series, places, threshold_mm_per_hour=args.threshold)
    )
    table = (
        scorecard(table, month=args.month) if args.kind else table.sort("wet_fraction")
    )

    # Reliability is only meaningful for the overall ranking, and it is
    # what tells the reader whether the ordering can be trusted at all.
    reliability = (
        split_half_reliability(series, threshold_mm_per_hour=args.threshold)
        if args.month is None
        else None
    )

    _print_table(table, threshold=args.threshold, month=args.month)

    metadata = build_metadata(
        start=args.start,
        end=args.end,
        threshold_mm_per_hour=args.threshold,
        place_count=table.height,
        reliability=reliability,
        extra={
            "report": {
                "kind_filter": args.kind or "all",
                "month": args.month,
                "columns": table.columns,
            }
        },
    )
    print("\nCaveats:")
    print(format_caveats(metadata))

    if args.output:
        path = Path(args.output)
        if path.suffix == ".csv":
            table.write_csv(path)
        else:
            table.write_parquet(path)
        sidecar = write_metadata(path, metadata)
        print(f"\nWrote {table.height} rows to {path}")
        print(f"Wrote provenance to {sidecar}")

    print(f"\nAttribution: {ARCHIVE_ATTRIBUTION}")
    print(f"Places: {PLACES_ATTRIBUTION}")
    return 0


def _print_table(table: pl.DataFrame, *, threshold: float, month: int | None) -> None:
    """Print a leaderboard, marking places that share a grid node."""
    scope = f"month {month:02d}" if month is not None else "all dates"
    print(f"Wet hours at > {threshold} mm/h, {scope}, driest first\n")
    header = f"{'#':>3}  {'place':<34} {'wet%':>6} {'hours':>7}"
    if "rain_mm" in table.columns:
        header += f" {'rain mm':>9}"
    if "tied" in table.columns:
        header += f" {'tied':>5}"
    print(header)
    print("-" * len(header))
    for rank, row in enumerate(table.iter_rows(named=True), start=1):
        line = (
            f"{rank:>3}  {row['name'][:34]:<34} "
            f"{100 * row['wet_fraction']:>5.1f}% "
            f"{row['wet_hours']:>7}"
        )
        if "rain_mm" in table.columns:
            line += f" {row['rain_mm']:>9.0f}"
        if "tied" in table.columns:
            line += f" {'yes' if row['tied'] else '':>5}"
        print(line)


if __name__ == "__main__":
    sys.exit(main())
