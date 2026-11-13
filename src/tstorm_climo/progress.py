"""Logging configuration and progress reporting for long-running work.

Library modules use plain ``logging.getLogger(__name__)`` and report
through the helpers here. Importing the package installs no handlers and
changes no logging configuration: only :func:`configure_logging`, called at
the CLI boundary, does that. An embedding application (the FastAPI service,
a notebook, a script) therefore keeps whatever logging setup it already has.

The CLI writes progress to **stderr** so stdout stays a clean result
channel, reports stage boundaries with elapsed time, and rate-limits
periodic counters. Percentages are only printed when a reliable total is
known; otherwise the message reports bytes or counts accumulated so far.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TextIO

# All package modules log beneath this name. The CLI configures one handler
# on it; applications configure their own handlers on the root logger and
# let these records propagate.
LOGGER_NAME = "tstorm_climo"

# Slow enough that a long scan or download does not flood a terminal, fast
# enough that a stalled job is obvious.
DEFAULT_PROGRESS_INTERVAL_SECONDS = 5.0

# Marks the handler installed by ``configure_logging`` so repeated CLI runs
# in one process (tests, embedded calls) reuse it instead of stacking
# handlers and duplicating every line.
_CLI_HANDLER_ATTRIBUTE = "_tstorm_climo_cli_handler"


class _DynamicStderrHandler(logging.StreamHandler):
    """A stderr handler that follows ``sys.stderr`` as it is replaced.

    Test harnesses and some applications swap ``sys.stderr`` after import.
    Resolving the stream at emit time keeps progress visible in that stream
    instead of writing to a closed or detached one. An explicit ``stream``
    opts out and stays fixed, which is what tests that pass a buffer want.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        super().__init__(stream if stream is not None else sys.stderr)
        self._dynamic = stream is None

    def follow(self, stream: TextIO | None) -> None:
        """Point the handler at ``stream``, or back at the live stderr."""
        self._dynamic = stream is None
        self.stream = stream if stream is not None else sys.stderr

    def emit(self, record: logging.LogRecord) -> None:
        if self._dynamic:
            self.stream = sys.stderr
        super().emit(record)


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure package logging for a command-line run.

    Args:
        verbose: Log at DEBUG, including detailed stage information.
        quiet: Log only warnings and errors, suppressing progress.
        stream: Destination for log lines. Defaults to ``sys.stderr``; tests
            pass a buffer to inspect output.

    Returns:
        The configured ``tstorm_climo`` logger.

    Raises:
        ValueError: If both ``verbose`` and ``quiet`` are requested.
    """
    if verbose and quiet:
        raise ValueError("verbose and quiet are mutually exclusive")
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    package = logging.getLogger(LOGGER_NAME)
    package.setLevel(level)

    handler = next(
        (
            candidate
            for candidate in package.handlers
            if getattr(candidate, _CLI_HANDLER_ATTRIBUTE, False)
        ),
        None,
    )
    if handler is None:
        handler = _DynamicStderrHandler(stream)
        setattr(handler, _CLI_HANDLER_ATTRIBUTE, True)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        package.addHandler(handler)
    elif isinstance(handler, _DynamicStderrHandler):
        handler.follow(stream)
    elif stream is not None and isinstance(handler, logging.StreamHandler):
        handler.setStream(stream)
    return package


class RateLimiter:
    """Decide when the next periodic progress line is due.

    The clock is injectable so tests can advance time without sleeping.

    Args:
        interval: Minimum seconds between reports. Zero reports every call.
        clock: Monotonic time source, in seconds.
    """

    def __init__(
        self,
        interval: float = DEFAULT_PROGRESS_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval < 0:
            raise ValueError(f"interval must be non-negative, got {interval}")
        self.interval = float(interval)
        self._clock = clock
        self._next_due = clock() + self.interval

    def due(self) -> bool:
        """Whether enough time has passed since the last report."""
        now = self._clock()
        if now < self._next_due:
            return False
        self._next_due = now + self.interval
        return True


@contextmanager
def stage(
    logger: logging.Logger,
    description: str,
    *,
    level: int = logging.INFO,
    clock: Callable[[], float] = time.monotonic,
) -> Generator[None]:
    """Log the start and end of one named stage, with elapsed time.

    On failure the stage is logged at DEBUG with the traceback and the
    exception propagates; the CLI reports the user-facing error once.
    """
    started = clock()
    logger.log(level, "%s...", description)
    try:
        yield
    except Exception:
        logger.debug(
            "%s failed after %s",
            description,
            format_duration(clock() - started),
            exc_info=True,
        )
        raise
    logger.log(
        level,
        "%s done in %s",
        description,
        format_duration(clock() - started),
    )


def format_bytes(count: int) -> str:
    """Render a byte count with binary units and one decimal place."""
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if unit == "B":
            if abs(value) < 1024:
                return f"{int(value)} B"
        elif abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def format_duration(seconds: float) -> str:
    """Render elapsed seconds compactly, from milliseconds to hours."""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60):02d}m"


def format_count(count: int) -> str:
    """Render a count with thousands separators."""
    return f"{count:,}"


class ByteProgress:
    """Report a byte stream's progress without one line per chunk.

    Percentages appear only when ``total`` is known, so an unknown content
    length is reported honestly as bytes and throughput so far.

    Args:
        logger: Logger that receives the progress lines.
        total: Expected byte count, or ``None`` when the source did not
            provide one.
        interval: Minimum seconds between periodic lines.
        clock: Monotonic time source, in seconds.
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        total: int | None,
        interval: float = DEFAULT_PROGRESS_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._logger = logger
        self.total = total
        self.received = 0
        self._clock = clock
        self._started = clock()
        self._limiter = RateLimiter(interval, clock=clock)

    def update(self, chunk_bytes: int) -> None:
        """Account for a received chunk, logging only when one is due."""
        self.received += chunk_bytes
        if self._limiter.due():
            self._report()

    def finish(self) -> None:
        """Log the final byte count, whether or not a periodic line is due."""
        self._report()

    def _report(self) -> None:
        elapsed = max(self._clock() - self._started, 0.001)
        rate = self.received / elapsed
        transferred = f"{format_bytes(self.received)} in {format_duration(elapsed)}"
        if self.total is not None and self.total > 0:
            percent = 100.0 * self.received / self.total
            transferred = (
                f"{format_bytes(self.received)} / {format_bytes(self.total)} "
                f"({percent:.1f}%)"
            )
        self._logger.info("Downloaded %s, %s/s", transferred, format_bytes(int(rate)))
