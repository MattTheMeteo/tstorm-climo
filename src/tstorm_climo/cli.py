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

    return 1


if __name__ == "__main__":
    sys.exit(main())
