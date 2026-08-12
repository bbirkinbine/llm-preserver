"""Tests for llm_preserver.cli.window — spec 0015.

One helper decides how many rows a discover frame may print, so the
non-TTY determinism guarantee is checkable in one place. These tests ARE
the API contract:

    NON_TTY_WINDOW_ROWS = 20
    MIN_WINDOW_ROWS = 5

    def resolve_window_size(stream: TextIO, chrome_lines: int) -> int

Contract:

- A stream that is not a terminal gets exactly ``NON_TTY_WINDOW_ROWS``
  rows, and the terminal size is never read to reach that answer — the
  ``isatty`` check comes FIRST. ``shutil.get_terminal_size`` honors the
  ``LINES`` environment variable, so reading it before the check would
  make piped output machine-dependent; a pipe, a cron run, and
  ``CliRunner`` must all produce the same bytes (the 0009 precedent:
  TTY-only progress rendering, non-TTY output unchanged).
- A terminal gets its height less the frame's fixed chrome, floored at
  ``MIN_WINDOW_ROWS`` so a very short terminal still shows a usable
  window instead of one row (or none).

The helper reads the height through ``shutil.get_terminal_size``, which
these tests monkeypatch as a module attribute.
"""

import io
import os
import shutil

from llm_preserver.cli.window import (
    MIN_WINDOW_ROWS,
    NON_TTY_WINDOW_ROWS,
    resolve_window_size,
)


class FakeStream(io.StringIO):
    """A text stream whose ``isatty`` answer the test chooses."""

    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def terminal_of(lines):
    """A ``get_terminal_size`` replacement reporting a fixed height."""

    def fake(*args, **kwargs):
        return os.terminal_size((80, lines))

    return fake


def test_a_piped_stream_gets_the_fixed_window_even_when_lines_is_set(monkeypatch):
    # The determinism guarantee: an inherited LINES must not change one
    # byte of piped output.
    monkeypatch.setenv("LINES", "120")
    monkeypatch.setenv("COLUMNS", "200")
    assert resolve_window_size(FakeStream(tty=False), 8) == NON_TTY_WINDOW_ROWS == 20


def test_a_piped_stream_never_reads_the_terminal_size(monkeypatch):
    calls = []

    def record(*args, **kwargs):
        calls.append(args)
        return os.terminal_size((80, 120))

    monkeypatch.setattr(shutil, "get_terminal_size", record)
    resolve_window_size(FakeStream(tty=False), 8)
    assert calls == []


def test_a_terminal_gets_its_height_less_the_frames_fixed_chrome(monkeypatch):
    monkeypatch.setattr(shutil, "get_terminal_size", terminal_of(50))
    assert resolve_window_size(FakeStream(tty=True), 8) == 42


def test_a_short_terminal_falls_back_to_the_minimum_window(monkeypatch):
    # 10 lines less 8 of chrome is 2 rows; show the floor instead.
    monkeypatch.setattr(shutil, "get_terminal_size", terminal_of(10))
    assert resolve_window_size(FakeStream(tty=True), 8) == MIN_WINDOW_ROWS == 5


def test_a_terminal_with_no_chrome_to_pay_for_gets_its_whole_height(monkeypatch):
    monkeypatch.setattr(shutil, "get_terminal_size", terminal_of(50))
    assert resolve_window_size(FakeStream(tty=True), 0) == 50
