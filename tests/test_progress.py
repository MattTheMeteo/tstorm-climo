"""Tests for logging configuration and rate-limited progress reporting."""

from __future__ import annotations

import io
import logging
import subprocess
import sys
from collections.abc import Iterator

import pytest

from tstorm_climo.progress import (
    ByteProgress,
    RateLimiter,
    configure_logging,
    format_bytes,
    format_count,
    format_duration,
    stage,
)

LOGGER_NAME = "tstorm_climo"


class FakeClock:
    """A monotonic clock the test moves by hand, so nothing sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def restore_package_logging() -> Iterator[None]:
    """Keep ``configure_logging`` calls from leaking between tests."""
    package = logging.getLogger(LOGGER_NAME)
    saved_level = package.level
    saved_handlers = list(package.handlers)
    yield
    package.setLevel(saved_level)
    package.handlers[:] = saved_handlers


@pytest.fixture
def captured_logger() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A logger with one plain-text handler, independent of root config."""
    stream = io.StringIO()
    logger = logging.getLogger("tstorm_climo.test_progress")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    try:
        yield logger, stream
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


def test_importing_the_package_installs_no_handlers() -> None:
    """The library must stay quiet until an entry point configures logging."""
    code = (
        "import logging\n"
        "import tstorm_climo, tstorm_climo.artifacts, tstorm_climo.cli\n"
        "import tstorm_climo.geofabrik, tstorm_climo.gridded\n"
        "logger = logging.getLogger('tstorm_climo')\n"
        "print(len(logger.handlers))\n"
        "print(logger.level)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == ["0", "0"]


def test_configure_logging_writes_only_to_the_given_stream() -> None:
    stderr = io.StringIO()
    logger = configure_logging(stream=stderr)

    logger.info("a progress line")

    assert "INFO a progress line" in stderr.getvalue()
    assert logger.level == logging.INFO


def test_configure_logging_maps_verbosity_to_levels() -> None:
    stderr = io.StringIO()
    assert configure_logging(stream=stderr).level == logging.INFO
    assert configure_logging(verbose=True, stream=stderr).level == logging.DEBUG
    assert configure_logging(quiet=True, stream=stderr).level == logging.WARNING


def test_configure_logging_reuses_its_handler() -> None:
    stderr = io.StringIO()
    package = configure_logging(stream=stderr)
    handlers = list(package.handlers)

    package = configure_logging(verbose=True, stream=stderr)
    package.info("one line")

    assert list(package.handlers) == handlers
    assert stderr.getvalue().count("one line") == 1


def test_verbose_and_quiet_conflict() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        configure_logging(verbose=True, quiet=True)


def test_rate_limiter_uses_the_injected_clock() -> None:
    clock = FakeClock()
    limiter = RateLimiter(interval=5.0, clock=clock)

    assert limiter.due() is False
    clock.advance(4.9)
    assert limiter.due() is False
    clock.advance(0.1)
    assert limiter.due() is True
    assert limiter.due() is False
    clock.advance(5.0)
    assert limiter.due() is True


def test_rate_limiter_rejects_a_negative_interval() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        RateLimiter(interval=-1)


def test_rate_limiter_interval_zero_reports_every_call() -> None:
    limiter = RateLimiter(interval=0, clock=FakeClock())
    assert limiter.due() is True
    assert limiter.due() is True


def test_format_bytes_uses_binary_units() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(1536) == "1.5 KiB"
    assert format_bytes(5 * 1024**3) == "5.0 GiB"


def test_format_duration_spans_milliseconds_to_hours() -> None:
    assert format_duration(0.25) == "250 ms"
    assert format_duration(2.5) == "2.5 s"
    assert format_duration(65) == "1m 05s"
    assert format_duration(3665) == "1h 01m"


def test_format_count_adds_thousands_separators() -> None:
    assert format_count(1_234_567) == "1,234,567"


def test_stage_logs_start_and_finish_with_elapsed_time(
    captured_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured_logger
    clock = FakeClock()

    with stage(logger, "Scanning PBF", clock=clock):
        clock.advance(2.5)

    assert stream.getvalue().splitlines() == [
        "Scanning PBF...",
        "Scanning PBF done in 2.5 s",
    ]


def test_stage_propagates_failure(
    captured_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured_logger

    with (
        pytest.raises(RuntimeError, match="boom"),
        stage(logger, "Writing artifact"),
    ):
        raise RuntimeError("boom")

    assert stream.getvalue().splitlines() == ["Writing artifact..."]


def test_byte_progress_reports_percent_and_rate_limits(
    captured_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured_logger
    clock = FakeClock()
    progress = ByteProgress(logger, total=1000, interval=5.0, clock=clock)

    progress.update(100)
    progress.update(100)
    assert stream.getvalue() == ""

    clock.advance(5.0)
    progress.update(300)
    progress.finish()

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert "500 B / 1000 B (50.0%)" in lines[0]
    assert "500 B / 1000 B (50.0%)" in lines[1]


def test_byte_progress_reports_bytes_when_the_total_is_unknown(
    captured_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured_logger
    progress = ByteProgress(logger, total=None, interval=0, clock=FakeClock())

    progress.update(2048)
    progress.finish()

    text = stream.getvalue()
    assert "2.0 KiB" in text
    assert "%" not in text
