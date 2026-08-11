"""Live TTY progress for a long verify run (spec 0009).

Renders only when the stream is a terminal, so cron and piped runs stay
byte-identical to a progress-free run.
"""

import time
from collections.abc import Callable
from typing import TextIO

from llm_preserver.pull_preflight import human_size
from llm_preserver.render import clean_text

_RENDER_INTERVAL_SECONDS = 0.5


class ProgressRenderer:
    """Live status on stderr while verify walks and hashes.

    Renders only when the stream is a terminal: a human staring at a
    multi-gigabyte hash gets a ``checking`` line per model and an
    in-place byte counter per file (adjudicated 2026-07-13 — silence
    during a long hash reads as a hang). Cron and piped runs see no
    progress output at all, so the report and exit-code contract stay
    byte-identical to a progress-free run.
    """

    def __init__(self, stream: TextIO, now: Callable[[], float] = time.monotonic) -> None:
        self._stream = stream
        self._enabled = stream.isatty()
        self._now = now
        self._file_label: str | None = None
        self._file_total: int | None = None
        self._file_done = 0
        self._last_render = 0.0
        self._last_width = 0

    def on_model_start(self, model_id: str, file_count: int, recorded_bytes: int) -> None:
        """One ``checking …`` line per model, before its files run."""
        if not self._enabled:
            return
        self.finish_line()
        noun = "file" if file_count == 1 else "files"
        line = f"checking {model_id} ({file_count} {noun}, {human_size(recorded_bytes)} recorded)"
        self._stream.write(clean_text(line, single_line=True) + "\n")
        self._stream.flush()

    def on_file_start(self, rel_path: str, size: int | None) -> None:
        """Arm the in-place byte counter for the file about to hash."""
        if not self._enabled:
            return
        self._file_label = clean_text(rel_path, single_line=True)
        self._file_total = size
        self._file_done = 0
        self._render_file_line()

    def on_file_bytes(self, count: int) -> None:
        """Advance the byte counter; redraw at most twice a second."""
        if not self._enabled or self._file_label is None:
            return
        self._file_done += count
        if self._now() - self._last_render >= _RENDER_INTERVAL_SECONDS:
            self._render_file_line()

    def finish_line(self) -> None:
        """Terminate any in-place counter line before normal output."""
        if not self._enabled:
            return
        if self._last_width:
            self._stream.write("\n")
            self._stream.flush()
        self._file_label = None
        self._last_width = 0

    def _render_file_line(self) -> None:
        total = f" / {human_size(self._file_total)}" if self._file_total is not None else ""
        text = f"  hashing {self._file_label}: {human_size(self._file_done)}{total}"
        # Carriage return + pad to the previous width: same-line update
        # with no ANSI beyond what every terminal handles.
        self._stream.write("\r" + text.ljust(self._last_width))
        self._stream.flush()
        self._last_width = len(text)
        self._last_render = self._now()
