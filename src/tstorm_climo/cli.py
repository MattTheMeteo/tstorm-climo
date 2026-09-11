"""Command-line interface for tstorm-climo."""

from __future__ import annotations

import argparse
import sys

from tstorm_climo.climatology import Climatology
from tstorm_climo.osm import KIND_TAGS, build_query, run_query
from tstorm_climo.places import deduplicate, from_overpass, save_csv


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="tstorm-climo",
        description=(
            "Build a precipitation climatology from HRRR analysis data."
        ),
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
        climo = Climatology()
        events = climo.summarize(args.start, args.end)
        for time, count in events.items():
            print(f"{time}: {count} grid points with precipitation")
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


if __name__ == "__main__":
    sys.exit(main())
