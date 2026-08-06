"""Tests for the window offsets in llm_preserver.discover_paging — spec 0015.

Split from test_discover_paging.py (300-line rule): that file pins the
append-only ``RowSequence``, this one pins the offsets a frame is cut
with. Pure, zero I/O, no ``cli`` imports. These tests ARE the API
contract:

    class WindowCursor:
        def __init__(self) -> None      # window [0, 0): nothing shown yet
        start: int                      # first index of the current window
        high_water: int                 # largest end index ever displayed
        back_available: bool
        def advance(self, end: int) -> None
        def back(self) -> None          # IndexError with no earlier window

    def fit_rows(rows: Sequence[NumberedRow], start: int, budget: int) -> int

``WindowCursor`` is a stack of half-open [start, end) windows.
``advance`` pushes a window starting where the previous one ended and
raises ``high_water`` to the end it displayed; ``back`` pops one and
never lowers ``high_water`` — rows already fetched and shown are not
un-shown by stepping back — and an ``advance`` after a ``back``
re-shows the window ``back`` left rather than skipping it. The first
window has no earlier window: ``back_available`` is False there and on
a fresh cursor, and ``back`` raises ``IndexError`` (stepping back off
the first frame would render zero rows).

``fit_rows`` returns the exclusive end index of a window starting at
``start`` that fits in ``budget`` LINES, charging one line per row plus
one for each relation-section header the slice emits — a header for the
slice's first row (the row above it is off-window, so the label must
reprint), and again wherever ``relation`` differs from the previous
row's. It always advances at least one row while rows remain, so a
terminal too short to pay for the header still makes progress instead
of looping on the same window, and it returns ``start`` unchanged once
``start >= len(rows)``.
"""

import pytest

from llm_preserver.discover_paging import NumberedRow, WindowCursor, fit_rows
from llm_preserver.hub_discovery import ModelSummary


def summary(repo_id, **overrides):
    """Build a ModelSummary with all-None facts unless overridden."""
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def rows_of(*relations):
    """Number one NumberedRow per relation given, in that order."""
    return [
        NumberedRow(number=number, summary=summary(f"acme/m{number}", relation=relation))
        for number, relation in enumerate(relations, start=1)
    ]


# --- WindowCursor -----------------------------------------------------


def test_a_fresh_cursor_has_shown_nothing_yet():
    cursor = WindowCursor()
    assert (cursor.start, cursor.high_water) == (0, 0)


def test_advance_starts_the_new_window_where_the_previous_one_ended():
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    assert cursor.start == 12


def test_advance_raises_the_high_water_to_the_end_it_displayed():
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    assert cursor.high_water == 24


def test_back_returns_to_the_previous_windows_start():
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    cursor.back()
    assert cursor.start == 0


def test_back_does_not_lower_the_high_water_mark():
    # What has been fetched and shown stays shown: paging forward again
    # must not re-fetch, and must not reprint rows the user has seen.
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    cursor.back()
    assert cursor.high_water == 24


def test_advance_after_back_reshows_the_window_back_left():
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    cursor.back()
    cursor.advance(24)
    assert cursor.start == 12


def test_back_walks_back_through_every_window_shown():
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    cursor.advance(36)
    cursor.back()
    cursor.back()
    assert cursor.start == 0


def test_back_is_unavailable_on_a_fresh_cursor():
    assert WindowCursor().back_available is False


def test_back_is_unavailable_on_the_first_window():
    # Stepping back off the first frame would render zero rows.
    cursor = WindowCursor()
    cursor.advance(12)
    assert cursor.back_available is False


def test_back_becomes_available_once_a_second_window_is_shown():
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    assert cursor.back_available is True


def test_back_is_unavailable_again_after_stepping_back_to_the_first_window():
    cursor = WindowCursor()
    cursor.advance(12)
    cursor.advance(24)
    cursor.back()
    assert cursor.back_available is False


def test_back_from_the_first_window_raises_index_error():
    cursor = WindowCursor()
    cursor.advance(12)
    with pytest.raises(IndexError):
        cursor.back()


def test_back_from_a_fresh_cursor_raises_index_error():
    with pytest.raises(IndexError):
        WindowCursor().back()


# --- fit_rows ---------------------------------------------------------


def test_a_budget_pays_for_one_section_header_plus_its_rows():
    assert fit_rows(rows_of(*["quantized"] * 10), 0, 5) == 4


def test_crossing_a_relation_boundary_charges_the_second_header():
    rows = rows_of("quantized", "quantized", *["finetune"] * 8)
    # header + 2 quantized rows + header + 1 finetune row = 5 lines.
    assert fit_rows(rows, 0, 5) == 3


def test_alternating_relations_charge_a_header_for_every_row():
    assert fit_rows(rows_of("quantized", "finetune", "quantized", "finetune"), 0, 5) == 2


def test_a_window_starting_mid_sequence_pays_for_its_own_first_header():
    assert fit_rows(rows_of(*["quantized"] * 10), 4, 5) == 8


def test_a_budget_of_one_still_advances_by_a_single_row():
    assert fit_rows(rows_of(*["quantized"] * 10), 0, 1) == 1


def test_an_ample_budget_takes_every_remaining_row():
    assert fit_rows(rows_of(*["quantized"] * 3), 0, 100) == 3


def test_a_start_at_the_end_of_the_rows_returns_the_start_unchanged():
    assert fit_rows(rows_of(*["quantized"] * 3), 3, 20) == 3


def test_an_empty_row_sequence_returns_the_start_unchanged():
    assert fit_rows([], 0, 20) == 0
