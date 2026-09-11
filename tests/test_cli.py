"""Tests for the CLI argument parsing and help output."""

import pytest

from tstorm_climo.cli import build_parser, main


def test_summarize_requires_dates() -> None:
    """The summarize subcommand should require --start and --end."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["summarize"])


def test_summarize_parses_dates() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["summarize", "--start", "2025-07-01", "--end", "2025-07-31"]
    )
    assert args.command == "summarize"
    assert args.start == "2025-07-01"
    assert args.end == "2025-07-31"


def test_main_unknown_command(capsys) -> None:
    """An unrecognized command should exit with a nonzero status."""
    with pytest.raises(SystemExit):
        main(["bogus"])
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err
