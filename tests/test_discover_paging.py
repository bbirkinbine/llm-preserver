"""Tests for the row sequence in llm_preserver.discover_paging — spec 0015.

Pins the append-only numbered sequence that replaces discover's
accumulate-and-reprint listing: pure, zero I/O, no ``cli`` imports.
The window offsets over it (``WindowCursor``, ``fit_rows``) live in
``test_discover_windows.py``. These tests ARE the API contract:

    @dataclass(frozen=True)
    class NumberedRow:
        number: int                     # the pick number, assigned once
        summary: ModelSummary

    class RowSequence:
        def __init__(self, pinned_count: int = 0) -> None
        def extend(self, summaries: Sequence[ModelSummary]) -> list[NumberedRow]
        rows: tuple[NumberedRow, ...]   # every row appended, in append order
        highest: int                    # pinned_count + len(rows)
        def __len__(self) -> int        # rows appended, pinned excluded

    @dataclass(frozen=True)
    class WindowFooter:
        first: int
        last: int
        highest: int
        more_available: bool
        back_available: bool

``pinned_count`` reserves the leading numbers a stage pins outside the
sequence (the tree's ancestry ladder), so appended rows start at
``pinned_count + 1``. ``extend`` assigns numbers at append time,
returns only the rows it appended, and NEVER re-sorts: a number that
has been displayed resolves to the same repo for the whole life of the
stage. That permanence is what spec 0015 exists to buy — a probe over
four relations of 40 children measured 60 of 80 numbers pointing at a
different repo after one ``m``, so the interleaved-relations shape
below is the load-bearing regression; a single-relation sequence
cannot catch it.

``WindowFooter`` only carries the facts a footer line needs; rendering
them is ``discover_render``'s job and is tested there.
"""

import pytest

from llm_preserver.discover_paging import NumberedRow, RowSequence, WindowFooter
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


def batch(prefix, relation, start, count):
    """Build ``count`` summaries ``<prefix>/child-NN`` under one relation."""
    return [
        summary(f"{prefix}/child-{index:02d}", relation=relation)
        for index in range(start, start + count)
    ]


# --- shapes -----------------------------------------------------------


def test_numbered_row_is_frozen():
    row = NumberedRow(number=1, summary=summary("acme/m0"))
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        row.number = 2  # type: ignore[misc]


def test_window_footer_is_frozen():
    footer = WindowFooter(first=21, last=40, highest=40, more_available=True, back_available=True)
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        footer.first = 1  # type: ignore[misc]


# --- numbering --------------------------------------------------------


def test_first_appended_row_takes_number_one_when_nothing_is_pinned():
    sequence = RowSequence()
    appended = sequence.extend([summary("acme/m0"), summary("acme/m1")])
    assert [row.number for row in appended] == [1, 2]


def test_pinned_count_reserves_the_leading_numbers_for_the_stage():
    # The tree pins its ancestry ladder outside the sequence: children
    # must start after it, or two rows answer to one number.
    sequence = RowSequence(pinned_count=4)
    appended = sequence.extend([summary("acme/m0"), summary("acme/m1")])
    assert [row.number for row in appended] == [5, 6]


def test_a_later_extend_continues_numbering_after_the_earlier_rows():
    sequence = RowSequence()
    sequence.extend([summary("acme/m0"), summary("acme/m1")])
    appended = sequence.extend([summary("acme/m2")])
    assert [row.number for row in appended] == [3]


def test_extend_returns_only_the_rows_it_appended():
    sequence = RowSequence()
    sequence.extend([summary("acme/m0"), summary("acme/m1")])
    appended = sequence.extend([summary("acme/m2"), summary("acme/m3")])
    assert [row.summary.repo_id for row in appended] == ["acme/m2", "acme/m3"]


def test_rows_accumulate_every_batch_in_append_order():
    sequence = RowSequence()
    sequence.extend([summary("acme/m0")])
    sequence.extend([summary("acme/m1"), summary("acme/m2")])
    assert [row.summary.repo_id for row in sequence.rows] == ["acme/m0", "acme/m1", "acme/m2"]


def test_rows_are_exposed_as_an_immutable_tuple():
    sequence = RowSequence()
    sequence.extend([summary("acme/m0")])
    assert isinstance(sequence.rows, tuple)


# --- the numbers-are-permanent invariant ------------------------------


def test_numbers_assigned_before_a_later_fetch_still_name_the_same_repos():
    sequence = RowSequence()
    first = sequence.extend([*batch("q", "quantized", 0, 20), *batch("f", "finetune", 0, 20)])
    before = {row.number: row.summary.repo_id for row in first}
    sequence.extend([*batch("q", "quantized", 20, 20), *batch("f", "finetune", 20, 20)])
    after = {row.number: row.summary.repo_id for row in sequence.rows}
    assert {number: after[number] for number in before} == before


def test_the_first_finetune_row_keeps_its_number_when_more_quants_arrive():
    # Probe measurement: pick 21 was f/child-00 before "m" and
    # q/child-20 after — read a number, page, type it, land elsewhere.
    sequence = RowSequence()
    sequence.extend([*batch("q", "quantized", 0, 20), *batch("f", "finetune", 0, 20)])
    sequence.extend([*batch("q", "quantized", 20, 20), *batch("f", "finetune", 20, 20)])
    pick_21 = next(row for row in sequence.rows if row.number == 21)
    assert pick_21.summary.repo_id == "f/child-00"


def test_a_later_batch_appends_at_the_end_and_is_never_regrouped_by_relation():
    sequence = RowSequence()
    sequence.extend([*batch("q", "quantized", 0, 2), *batch("f", "finetune", 0, 2)])
    sequence.extend([*batch("q", "quantized", 2, 2), *batch("f", "finetune", 2, 2)])
    assert [row.summary.relation for row in sequence.rows] == [
        "quantized",
        "quantized",
        "finetune",
        "finetune",
        "quantized",
        "quantized",
        "finetune",
        "finetune",
    ]


# --- counters ---------------------------------------------------------


def test_highest_starts_at_the_pinned_count_before_anything_is_appended():
    assert RowSequence().highest == 0
    assert RowSequence(pinned_count=4).highest == 4


def test_highest_reports_the_largest_number_assigned_so_far():
    sequence = RowSequence(pinned_count=4)
    sequence.extend([summary(f"acme/m{i}") for i in range(3)])
    assert sequence.highest == 7
    sequence.extend([summary(f"acme/n{i}") for i in range(2)])
    assert sequence.highest == 9


def test_len_counts_appended_rows_and_ignores_the_pinned_reservation():
    sequence = RowSequence(pinned_count=4)
    sequence.extend([summary("acme/m0"), summary("acme/m1")])
    assert len(sequence) == 2


def test_extending_with_no_summaries_leaves_the_numbering_untouched():
    # An exhausted relation hands back an empty page; that must not
    # burn a number or disturb what is already on screen.
    sequence = RowSequence()
    sequence.extend([summary("acme/m0")])
    assert sequence.extend([]) == []
    assert sequence.highest == 1
    assert len(sequence.rows) == 1
