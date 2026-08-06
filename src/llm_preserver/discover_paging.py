"""Append-only row numbering and window offsets (spec 0015): pure, zero I/O.

Spec 0006 paged by accumulating rows and re-rendering the whole listing
on every ``m``, re-sorting the accumulated list by relation each time.
Two costs followed: frames grew without bound (89 lines on tree entry,
169 after one page), and a newly fetched page inserted *ahead of* rows
already numbered on screen, so 60 of 80 pick numbers silently changed
which repo they named.

This module removes both at the source. Rows are numbered once, when
they are appended, and the sequence only ever grows at the tail — so a
number is a permanent name for a repo. Rendering then walks the
sequence one terminal-sized window at a time instead of reprinting it.

Everything here is decision-shaped and testable without a TTY; the
window *size* (which needs a real terminal) is resolved in the CLI
layer, and rendering lives in ``discover_render``.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from llm_preserver.hub_discovery import ModelSummary


@dataclass(frozen=True)
class NumberedRow:
    """One listing row bound to the number the human types to pick it.

    Attributes:
        number: The pick key, assigned when the row was appended and
            never reassigned.
        summary: The hub facts for the row.
    """

    number: int
    summary: ModelSummary


@dataclass(frozen=True)
class WindowFooter:
    """What the footer line needs to orient a human in a long listing.

    Attributes:
        first: Lowest pick number in the visible window.
        last: Highest pick number in the visible window.
        highest: Largest number assigned so far, visible or scrolled off.
        more_available: True when the hub still has rows to fetch.
        back_available: True when an earlier window can be re-shown.
    """

    first: int
    last: int
    highest: int
    more_available: bool
    back_available: bool


@dataclass
class RowSequence:
    """Rows in fetch order, numbered at append time and never re-sorted.

    The no-re-sort property is the whole point: grouping the
    accumulated rows by relation on every page is what made a later
    quantized page displace the finetune rows already numbered on
    screen. Sections repeating across batches is the honest rendering
    of what was actually fetched.

    Attributes:
        pinned_count: How many leading numbers belong to rows the stage
            pins outside this sequence — the tree's ancestry ladder,
            fetched once on entry and never paged. Appended rows start
            numbering after it.
    """

    pinned_count: int = 0
    _rows: list[NumberedRow] = field(default_factory=list, repr=False)

    def extend(self, summaries: Sequence[ModelSummary]) -> list[NumberedRow]:
        """Append a batch, numbering each row as it lands.

        Args:
            summaries: The newly fetched rows, in hub order.

        Returns:
            Just the rows appended by this call, so a caller can render
            a batch without re-walking the sequence.
        """
        appended = [
            NumberedRow(number=self.highest + offset, summary=summary)
            for offset, summary in enumerate(summaries, start=1)
        ]
        self._rows.extend(appended)
        return appended

    @property
    def rows(self) -> tuple[NumberedRow, ...]:
        """Every row appended so far, in append order."""
        return tuple(self._rows)

    @property
    def highest(self) -> int:
        """The largest pick number assigned so far."""
        return self.pinned_count + len(self._rows)

    def __len__(self) -> int:
        """How many rows were appended (the pinned reservation is not one)."""
        return len(self._rows)


class WindowCursor:
    """A stack of the windows shown so far, for ``m`` forward / ``b`` back.

    Window ends are data-dependent — a frame's row count depends on how
    many section headers its slice emits — so stepping back cannot be
    ``start - size`` arithmetic. The cursor remembers the windows it
    actually displayed and pops them.

    ``high_water`` is deliberately never lowered by ``back``: it marks
    how far the human has seen, which is what decides both "has this
    row been offered yet" and where the next ``m`` resumes.
    """

    def __init__(self) -> None:
        self._windows: list[tuple[int, int]] = []
        self._high_water = 0

    @property
    def start(self) -> int:
        """Index of the first row in the current window; 0 before any."""
        return self._windows[-1][0] if self._windows else 0

    @property
    def end(self) -> int:
        """Exclusive index one past the current window's last row.

        Where the next ``m`` starts. After a ``b`` this is behind
        ``high_water``, which is what makes ``m`` re-show the window
        stepped back from instead of skipping it.
        """
        return self._windows[-1][1] if self._windows else 0

    @property
    def high_water(self) -> int:
        """Exclusive index one past the furthest row ever displayed."""
        return self._high_water

    @property
    def back_available(self) -> bool:
        """True when an earlier window exists to step back to."""
        return len(self._windows) > 1

    def advance(self, end: int) -> None:
        """Show a new window running from the current window's end to ``end``.

        Args:
            end: Exclusive row index the new window stops at.
        """
        start = self._windows[-1][1] if self._windows else 0
        self._windows.append((start, end))
        self._high_water = max(self._high_water, end)

    def back(self) -> None:
        """Return to the previous window.

        Raises:
            IndexError: When no earlier window exists — stepping back
                off the first frame would render nothing, so it is a
                caller bug rather than a silent no-op.
        """
        if not self.back_available:
            raise IndexError("no earlier window to step back to")
        self._windows.pop()


def fit_rows(
    rows: Sequence[NumberedRow],
    start: int,
    budget: int,
    *,
    line_cost: Callable[[NumberedRow], int] | None = None,
) -> int:
    """Find where a window starting at ``start`` must stop to fit ``budget`` lines.

    A relation-section header costs one line. The first row of any
    window pays for a header even mid-section, because a frame that
    cannot be scrolled back to must say what it is showing. Rows with
    no relation (the search stage, which has no sections) are charged
    for themselves only.

    Args:
        rows: The append-only sequence being windowed.
        start: Index of the window's first row.
        budget: Lines available for rows and their section headers.
        line_cost: How many physical lines a row occupies. Defaults to
            one per row, which is right for piped output and keeps it
            byte-identical; a terminal passes a width-aware cost,
            because a 98-character hub row wraps to two lines at 80
            columns and a frame that ignored that would promise one
            screen and fill two (review round, 2026-08-06).

    Returns:
        The exclusive end index. Always at least ``start + 1`` while
        rows remain — a terminal too short for one row must still be
        able to page — and ``start`` exactly when nothing remains.
    """
    if start >= len(rows):
        return start
    lines = 0
    end = start
    previous_relation: str | None = None
    for index in range(start, len(rows)):
        row = rows[index]
        relation = row.summary.relation
        header = relation is not None and (index == start or relation != previous_relation)
        cost = (line_cost(row) if line_cost is not None else 1) + (1 if header else 0)
        if lines + cost > budget and end > start:
            break
        lines += cost
        end = index + 1
        previous_relation = relation
    return end
