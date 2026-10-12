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


def test_build_observations_parses_arguments() -> None:
    args = build_parser().parse_args(
        [
            "build-observations",
            "--places",
            "places.csv",
            "--start",
            "2018-01-01",
            "--end",
            "2025-12-31",
            "--output",
            "artifacts/rain",
        ]
    )
    assert args.command == "build-observations"
    assert args.places == "places.csv"
    assert args.start == "2018-01-01"
    assert args.end == "2025-12-31"
    assert args.output == "artifacts/rain"


def test_build_observations_command_runs_builder(monkeypatch, tmp_path, capsys) -> None:
    import tstorm_climo.cli as cli_module

    places = [object()]
    monkeypatch.setattr(cli_module, "load_csv", lambda _path: places)
    manifest = {
        "observations": {"row_count": 2, "valid_fraction": 0.5},
        "place_catalog": {"place_count": 1},
        "date_range": {
            "observed_start": "2025-07-15T00:00:00+00:00",
            "observed_end": "2025-07-15T01:00:00+00:00",
        },
    }
    calls = []

    def build(observed_places, start, end, output):
        calls.append((observed_places, start, end, output))
        return manifest

    monkeypatch.setattr(cli_module, "build_hourly_observation_artifact", build)
    output = tmp_path / "hourly-rain"
    assert (
        main(
            [
                "build-observations",
                "--places",
                "places.csv",
                "--start",
                "2025-07-15",
                "--end",
                "2025-07-15",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert calls == [(places, "2025-07-15", "2025-07-15", str(output))]
    assert "2 observations" in capsys.readouterr().out


def test_main_unknown_command(capsys) -> None:
    """An unrecognized command should exit with a nonzero status."""
    with pytest.raises(SystemExit):
        main(["bogus"])
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err
