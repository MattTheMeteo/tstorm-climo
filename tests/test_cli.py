"""Tests for the CLI argument parsing and help output."""

import json

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


def test_build_catalog_parses_geofabrik_and_s3_options() -> None:
    args = build_parser().parse_args(
        [
            "build-catalog",
            "--source",
            "geofabrik",
            "--region",
            "north-america/us/massachusetts",
            "--kind",
            "golf_course",
            "--kind",
            "beach",
            "--output",
            "catalogs/massachusetts",
            "--s3-bucket",
            "weather-data",
        ]
    )
    assert args.source == "geofabrik"
    assert args.region == "north-america/us/massachusetts"
    assert args.kind == ["golf_course", "beach"]
    assert args.s3_bucket == "weather-data"


def test_catalog_s3_prefix_scopes_by_region_and_bbox() -> None:
    from tstorm_climo.cli import _catalog_s3_prefix

    parser = build_parser()
    regional = parser.parse_args(
        [
            "build-catalog",
            "--region",
            "north-america/us/massachusetts",
            "--kind",
            "beach",
            "--output",
            "catalog",
        ]
    )
    boxed = parser.parse_args(
        [
            "build-catalog",
            "--region",
            "north-america/us/massachusetts",
            "--kind",
            "beach",
            "--bbox",
            "42.35",
            "-71.20",
            "42.95",
            "-70.50",
            "--output",
            "catalog",
        ]
    )
    assert _catalog_s3_prefix(regional) == (
        "tstorm-climo/catalogs/geofabrik/north-america/us/massachusetts/full"
    )
    assert _catalog_s3_prefix(boxed) == (
        "tstorm-climo/catalogs/geofabrik/north-america/us/massachusetts/"
        "bbox-42p3500-m71p2000-42p9500-m70p5000"
    )


def test_layercake_source_is_rejected() -> None:
    """The removed source must fail loudly rather than fall back."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "build-catalog",
                "--source",
                "layercake",
                "--kind",
                "beach",
                "--output",
                "catalog",
            ]
        )


def test_build_catalog_requires_a_region() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "build-catalog",
                "--kind",
                "beach",
                "--output",
                "catalog",
            ]
        )


def test_build_catalog_publishes_only_after_local_release(
    monkeypatch, tmp_path, capsys
) -> None:
    import tstorm_climo.cli as cli_module

    release = tmp_path / "release"
    release.mkdir()
    (release / "manifest.json").write_text(
        json.dumps(
            {
                "place_count": 2,
                "catalog": {"counts": {"golf_course": 2}},
            }
        )
    )
    calls = []
    monkeypatch.setattr(
        cli_module,
        "build_geofabrik_catalog",
        lambda region, kinds, output, bbox=None: release,
    )

    def publish(catalog_dir, bucket, *, prefix):
        calls.append((catalog_dir, bucket, prefix))
        return {
            "release_id": "release-1",
            "bucket": bucket,
            "latest_key": f"{prefix}/latest.json",
        }

    monkeypatch.setattr(cli_module, "publish_catalog_to_s3", publish)
    assert (
        main(
            [
                "build-catalog",
                "--source",
                "geofabrik",
                "--region",
                "north-america/us/massachusetts",
                "--kind",
                "golf_course",
                "--output",
                str(release),
                "--s3-bucket",
                "weather-data",
            ]
        )
        == 0
    )
    assert calls == [
        (
            release,
            "weather-data",
            "tstorm-climo/catalogs/geofabrik/north-america/us/massachusetts/full",
        )
    ]
    assert "Published release-1" in capsys.readouterr().out


def test_build_observations_command_runs_builder(monkeypatch, tmp_path, capsys) -> None:
    import tstorm_climo.cli as cli_module

    places = [object()]
    monkeypatch.setattr(cli_module, "load_places", lambda _path: places)
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
