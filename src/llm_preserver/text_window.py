"""The physical-line arithmetic every windowed listing shares.

There is exactly **one** rule in this codebase for how many lines a
frame's contents occupy, and it lives here. Spec 0015 learned why the
hard way: a window sized in *logical* lines promised one screen and
delivered 39-45 physical rows on a 24-line terminal, because real hub
ids render 90-100 characters and wrap at 80 columns. Spec 0018 needed
the same rule for ``pull``'s file listing and found that neither
``fit_rows`` nor ``row_line_cost`` could be called from there —
``fit_rows`` reads a relation off a ``NumberedRow`` and
``row_line_cost`` renders ``  {n}. {repo_id}``, and a file row is
neither. Only the arithmetic was common, so the arithmetic is what got
extracted. A second copy of this rule is the bug, not the duplication.

Both functions are deliberately ignorant of what a row *is*: they take
text and integers. Whatever knows how to render a row builds the costs.
"""

import math
from collections.abc import Sequence


def wrapped_height(text: str, width: int | None) -> int:
    """Physical lines ``text`` occupies at ``width``; 1 when unknown.

    Args:
        text: One logical line, already rendered.
        width: Terminal columns, or None for a piped or broken stream.

    Returns:
        The wrapped height, or 1 when the width is unknown — which
        keeps piped output flat and therefore byte-identical across
        machines.
    """
    if width is None or width <= 0:
        return 1
    return max(1, math.ceil(len(text) / width))


def fit_by_cost(costs: Sequence[int], start: int, budget: int) -> int:
    """Find where a window starting at ``start`` must stop to fit ``budget``.

    Args:
        costs: Physical lines each item costs, in display order. An
            item may be charged for more than itself — a section header
            that must reprint above it, for instance — because the
            caller owns what a "cost" includes.
        start: Index of the window's first item.
        budget: Lines available for the whole window.

    Returns:
        The exclusive end index. Always at least ``start + 1`` while
        items remain — a terminal too short for a single item must
        still be able to page past it, or ``m`` could never advance —
        and ``start`` exactly when nothing remains.
    """
    if start >= len(costs):
        return start
    lines = 0
    end = start
    for index in range(start, len(costs)):
        if lines + costs[index] > budget and end > start:
            break
        lines += costs[index]
        end = index + 1
    return end
