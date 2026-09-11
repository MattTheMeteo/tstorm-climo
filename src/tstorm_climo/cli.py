"""Command-line interface for tstorm-climo."""

from __future__ import annotations

import argparse
import sys

from tstorm_climo.climatology import Climatology


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
        help="Summarize precipitation events from a set of HRRR analysis files.",
    )
    summarize.add_argument(
        "inputs",
        nargs="+",
        metavar="FILE",
        help="HRRR analysis files (GRIB2) to summarize.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the tstorm-climo console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "summarize":
        climo = Climatology()
        events = climo.add_precipitation_events(args.inputs)
        for time, count in sorted(events.items()):
            print(f"{time}: {count} grid points with precipitation")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
