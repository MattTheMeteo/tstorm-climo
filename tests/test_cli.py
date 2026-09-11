"""Tests for the CLI argument parsing and help output."""

import pytest

from tstorm_climo.cli import build_parser, main


def test_summarize_requires_files() -> None:
    """The summarize subcommand should require at least one input file."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["summarize"])


def test_summarize_parses_files() -> None:
    parser = build_parser()
    args = parser.parse_args(["summarize", "a.grib2", "b.grib2"])
    assert args.command == "summarize"
    assert args.inputs == ["a.grib2", "b.grib2"]


def test_main_unknown_command(capsys) -> None:
    """An unrecognized command should exit with a nonzero status."""
    with pytest.raises(SystemExit):
        main(["bogus"])
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err
