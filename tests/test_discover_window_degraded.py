"""Window sizing under degraded streams and wrapped rows (spec 0015 review round).

Two findings from the security review, both reproduced before the fix:

- ``resolve_window_size`` dereferenced the stream directly, so a
  detached stdout (CPython sets ``sys.stdout`` to None when fd 1 is
  invalid — ``llm-preserver discover ... >&-``) raised
  ``AttributeError`` and a closed stream raised ``ValueError``, under a
  rich traceback. Click's own ``echo`` guards for exactly this, so
  every *other* line of the command degrades quietly; this call ran
  before any output and turned a silent run into a crash. Same
  0011/0012 class this repo keeps hitting: a degraded environment must
  produce a clean outcome, never a traceback.
- ``fit_rows`` charged one line per row, but rows are logical lines.
  Real hub ids render 90-98 characters (measured against live listings
  2026-08-06), which wrap to two physical rows at 80 columns — so a
  20-row window could occupy ~40 physical lines and blow the "no frame
  exceeds one screen" guarantee on exactly the no-scrollback consoles
  the spec was written for. The budget now charges each row its
  wrapped height when the width is known.

Non-TTY runs keep a width of None and a flat one-line-per-row cost, so
piped output stays byte-identical (the determinism criterion).
"""

import io
import math

from llm_preserver.cli.window import (
    NON_TTY_WINDOW_ROWS,
    resolve_window_size,
    resolve_window_width,
)
from llm_preserver.discover_paging import NumberedRow, fit_rows
from llm_preserver.hub_discovery import ModelSummary


class FakeStream:
    """A stream whose isatty answer, or failure, is chosen by the test."""

    def __init__(self, *, tty: bool = False, raises: Exception | None = None) -> None:
        self._tty = tty
        self._raises = raises

    def isatty(self) -> bool:
        if self._raises is not None:
            raise self._raises
        return self._tty


def row(number: int, repo_id: str, relation: str | None = "quantized") -> NumberedRow:
    """One numbered row carrying only the facts the sizing math reads."""
    return NumberedRow(
        number=number,
        summary=ModelSummary(
            repo_id=repo_id,
            downloads=None,
            last_modified=None,
            gated=None,
            base_model=None,
            relation=relation,
        ),
    )


# --- degraded streams ---------------------------------------------------


def test_a_detached_stdout_falls_back_to_the_piped_window() -> None:
    # CPython hands us None for sys.stdout when fd 1 is invalid.
    assert resolve_window_size(None, 5) == NON_TTY_WINDOW_ROWS


def test_a_closed_stream_falls_back_to_the_piped_window() -> None:
    stream = io.StringIO()
    stream.close()
    assert resolve_window_size(stream, 5) == NON_TTY_WINDOW_ROWS


def test_a_stream_whose_isatty_raises_falls_back_to_the_piped_window() -> None:
    stream = FakeStream(raises=ValueError("I/O operation on closed file."))
    assert resolve_window_size(stream, 5) == NON_TTY_WINDOW_ROWS


def test_a_detached_stdout_reports_no_width() -> None:
    # None width means "do not wrap-adjust", which keeps the flat cost.
    assert resolve_window_width(None) is None


def test_a_closed_stream_reports_no_width() -> None:
    stream = io.StringIO()
    stream.close()
    assert resolve_window_width(stream) is None


def test_a_piped_stream_reports_no_width() -> None:
    assert resolve_window_width(FakeStream(tty=False)) is None


def test_a_terminal_reports_its_column_count(monkeypatch) -> None:
    monkeypatch.setattr(
        "llm_preserver.cli.window.shutil.get_terminal_size",
        lambda: os_terminal_size(columns=100, lines=40),
    )
    assert resolve_window_width(FakeStream(tty=True)) == 100


def os_terminal_size(*, columns: int, lines: int):
    """Stand-in for os.terminal_size with the two fields we read."""
    import os

    return os.terminal_size((columns, lines))


# --- wrapped rows cost their real height --------------------------------

LONG_ID = "Zynerji/Ektome-Mistral-7B-Instruct-v0.2-PristinelyUncensored"


def test_a_row_too_wide_for_the_terminal_costs_two_lines() -> None:
    # The measured real-world case: a 60-char hub id renders past 80
    # columns, so the frame must charge it what it actually occupies.
    rows = [row(number, LONG_ID) for number in range(1, 11)]
    text_width = len(f"  1. {LONG_ID}")
    assert text_width > 40  # the row genuinely exceeds the width used below

    def line_cost(_: NumberedRow) -> int:
        return math.ceil(text_width / 40)

    # Budget 9: one section header (1) + four rows at 2 lines each = 9.
    assert fit_rows(rows, 0, 9, line_cost=line_cost) == 4


def test_the_default_cost_is_one_line_per_row() -> None:
    # No width known (piped output): the flat cost, unchanged, so every
    # pre-existing count and the determinism guarantee still hold.
    rows = [row(number, LONG_ID) for number in range(1, 11)]
    assert fit_rows(rows, 0, 9) == 8  # 1 header + 8 rows


def test_a_wrapped_row_still_advances_the_window_on_a_tiny_budget() -> None:
    # The progress floor survives wrap-aware costing: a row taller than
    # the whole budget must still be shown, or "m" would livelock.
    rows = [row(number, LONG_ID) for number in range(1, 4)]
    assert fit_rows(rows, 0, 1, line_cost=lambda _: 9) == 1
