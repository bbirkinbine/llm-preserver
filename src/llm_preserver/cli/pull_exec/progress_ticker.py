"""The ``pull --progress`` ticker: one plain line every 30 seconds.

A daemon thread reads the meter on a fixed interval and logs one
self-contained line (spec 0026 criterion 1). It runs only between the
transfer-start seam and the end of the pull, so nothing prints while a
human answers a prompt, and it is stopped before an interrupt's resume
hint prints, so the hint stays the final line.

Printing progress must never cost the pull anything (criterion 6): a
line that cannot be rendered or written stops the ticker quietly, and
stopping it never raises or waits longer than a second.
"""

import logging
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Protocol

from llm_preserver.pull_progress import ProgressSnapshot, PullProgress, render_progress_line

PROGRESS_INTERVAL_SECONDS = 30
"""Seconds between progress lines; fixed, not configurable (spec 0026)."""

PROGRESS_LOGGER_NAME = "llm_preserver.pull_progress"
"""Logger the lines go through, so they carry the same ``LEVEL name:``
prefix and handler as the ``downloading n of m`` lines (criterion 1)."""

THREAD_NAME = "pull-progress-ticker"

_JOIN_TIMEOUT_SECONDS = 1.0

progress_logger = logging.getLogger(PROGRESS_LOGGER_NAME)
logger = logging.getLogger(__name__)


def log_progress_line(line: str) -> None:
    """Write one progress or summary line at INFO on the progress logger."""
    progress_logger.info("%s", line)


class _StopEvent(Protocol):
    """The two ``threading.Event`` methods the ticker uses (a test seam)."""

    def wait(self, timeout: float | None = None) -> bool: ...

    def set(self) -> None: ...


class ProgressTicker:
    """Emit a progress line on a fixed interval while a pull transfers.

    Args:
        meter: The pull's progress state.
        emit: Receives each rendered line.
        interval: Seconds between lines.
        event_factory: Builds the stop event; injected so tests drive
            ticks without sleeping.
    """

    def __init__(
        self,
        meter: PullProgress,
        emit: Callable[[str], None] = log_progress_line,
        interval: float = PROGRESS_INTERVAL_SECONDS,
        event_factory: Callable[[], _StopEvent] = threading.Event,
    ) -> None:
        self._meter = meter
        self._emit = emit
        self._interval = interval
        self._stop = event_factory()
        self._thread: threading.Thread | None = None

    def arm(self) -> None:
        """Start the ticker thread; a second call does nothing.

        A thread that cannot start (a pids-limited container raises
        ``RuntimeError``) leaves the pull running with no progress
        lines: progress is never worth a failed pull (criterion 6).
        """
        if self._thread is not None:
            return
        thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        try:
            thread.start()
        except RuntimeError:
            logger.debug("progress ticker could not start", exc_info=True)
            return
        self._thread = thread

    def _run(self) -> None:
        previous: ProgressSnapshot | None = None
        try:
            while not self._stop.wait(self._interval):
                if not self._meter.started:
                    continue
                snapshot = self._meter.snapshot()
                self._emit(render_progress_line(snapshot, previous))
                previous = snapshot
        except Exception:
            logger.debug("progress ticker stopped after a failure", exc_info=True)

    def __enter__(self) -> "ProgressTicker":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(_JOIN_TIMEOUT_SECONDS)
        except Exception:
            logger.debug("progress ticker did not stop cleanly", exc_info=True)
