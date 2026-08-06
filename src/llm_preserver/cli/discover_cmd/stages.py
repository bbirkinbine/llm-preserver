"""The two windowed discovery stages: search listing and model tree.

Spec 0006 defined the stages; spec 0015 changed how they page. A page
used to be the whole accumulated listing, re-sorted by relation and
reprinted on every ``m`` — which grew frames without bound and, worse,
renumbered rows the human had already read. Now rows are numbered once
as they arrive (``RowSequence``) and each frame renders one
terminal-sized window of them (``WindowCursor``), so a number is a
permanent name for a repo and no frame outgrows the screen.

Fetching and displaying are separate concerns here: one hub fetch
feeds however many windows it takes to show it.
"""

import sys
from collections.abc import Callable

import typer

from llm_preserver.cli.discover_cmd.prompts import begin_frame, prompt_pick
from llm_preserver.cli.discover_cmd.window import resolve_window_size, resolve_window_width
from llm_preserver.discover import DiscoveryPage, Option, build_parent_chain
from llm_preserver.discover_paging import (
    NumberedRow,
    RowSequence,
    WindowCursor,
    WindowFooter,
    fit_rows,
)
from llm_preserver.discover_render import (
    key_hints,
    render_search_page,
    render_tree_page,
    row_line_cost,
    tree_chrome_lines,
)
from llm_preserver.hub import HubClientProtocol
from llm_preserver.hub_discovery import ModelSummary, RelationType
from llm_preserver.render import clean_text

# Fixed relation order for tree children (spec 0006): deterministic,
# and the most preservation-relevant group (quantized) lists first.
_RELATION_ORDER: tuple[RelationType, ...] = ("quantized", "finetune", "adapter", "merge")

# Answer to an "m" that turns up nothing. Spec 0015: the hub publishes
# no total and a pager only proves itself exhausted after a short page,
# so a relation holding an exact multiple of PAGE_SIZE rows used to
# reprint the entire frame having fetched nothing.
NO_MORE_ROWS = "no further rows on the hub"

# Lines a search frame spends on something other than rows: the header,
# the footer, the frame rule, its blank line, and the prompt.
_SEARCH_CHROME = 5


def _next_window(
    cursor: WindowCursor,
    sequence: RowSequence,
    fetch: Callable[[], list[ModelSummary]],
    budget: int,
    line_cost: Callable[[NumberedRow], int] | None = None,
) -> bool:
    """Show the next window, topping the buffer up when it underfills one.

    Fetching when the buffer merely *empties* leaves the tail of a
    batch as its own runt frame: a 20-row hub page against a 19-line
    window measured windows of 19, 1, 19, 1 on an 80x24 terminal — a
    single result line under five lines of chrome, in the command whose
    whole purpose is making paging bearable (review round, 2026-08-06).
    Topping up when the remainder cannot fill a window keeps every
    frame full. Fetch *granularity* is untouched: still one page per
    relation, all relations advanced together (0006).

    Args:
        cursor: The window stack for this stage.
        sequence: The numbered rows fetched so far.
        fetch: Pulls the next batch from the hub; returns [] when dry.
        budget: Lines the frame may spend on rows and section headers.
        line_cost: Physical lines per row; None charges one each.

    Returns:
        False when there is nothing further to show — the caller says
        so in one line rather than reprinting an unchanged frame.
    """
    if len(sequence) - cursor.end < budget:
        sequence.extend(fetch())
    if cursor.end >= len(sequence):
        return False
    cursor.advance(fit_rows(sequence.rows, cursor.end, budget, line_cost=line_cost))
    return True


def _visible(
    sequence: RowSequence, cursor: WindowCursor, *, more_available: bool
) -> tuple[tuple[NumberedRow, ...], WindowFooter | None]:
    """Slice the current window and describe where it sits.

    Returns:
        The window's rows and its footer; an empty window (a tree with
        no children) gets no footer at all.
    """
    rows = sequence.rows[cursor.start : cursor.end]
    if not rows:
        return (), None
    return rows, WindowFooter(
        first=rows[0].number,
        last=rows[-1].number,
        # Numbers HANDED OUT, not rows fetched: the pick set is bounded
        # by the high-water mark, so counting fetched rows advertised
        # numbers the prompt then refused (review round, 2026-08-06).
        highest=sequence.pinned_count + cursor.high_water,
        more_available=more_available,
        back_available=cursor.back_available,
    )


def _continues_section(sequence: RowSequence, cursor: WindowCursor) -> bool:
    """True when this window opens mid-section, so its label says so."""
    if cursor.start == 0 or cursor.start >= cursor.end:
        return False
    rows = sequence.rows
    return rows[cursor.start].summary.relation == rows[cursor.start - 1].summary.relation


def _offered_options(sequence: RowSequence, cursor: WindowCursor) -> list[Option]:
    """Every row displayed so far, still pickable after scrolling off.

    Bounded by the high-water mark, not the visible window: a number
    read two windows ago must still work (that is the point of
    numbering rows permanently), while a row fetched into the buffer
    but never yet shown must not — the human cannot have read it.
    """
    return [
        Option(key=str(row.number), kind="navigate", summary=row.summary)
        for row in sequence.rows[: cursor.high_water]
    ]


def search_stage(client: HubClientProtocol, query: str) -> ModelSummary | None:
    """Run the search stage; return the picked repo or None to quit."""
    pager = client.search_models(query)
    sequence = RowSequence()
    cursor = WindowCursor()
    budget = resolve_window_size(sys.stdout, _SEARCH_CHROME)
    line_cost = row_line_cost(resolve_window_width(sys.stdout))
    if not _next_window(cursor, sequence, pager.next_page, budget, line_cost):
        message = f"no hub results for '{query}' — refine the query and re-run"
        typer.echo(clean_text(message, single_line=True))
        return None
    render = True
    while True:
        more = cursor.end < len(sequence) or not pager.exhausted
        rows, footer = _visible(sequence, cursor, more_available=more)
        if render:
            begin_frame()
            for line in render_search_page(rows, query=query, footer=footer):
                typer.echo(line)
        render = True
        page = DiscoveryPage(
            stage="search",
            options=tuple(_offered_options(sequence, cursor)),
            more_available=more,
            back_available=cursor.back_available,
        )
        hints = key_hints(more_available=more, back_available=cursor.back_available)
        pick = prompt_pick(page, f"pick a model to explore (number; {hints})")
        if pick.kind == "quit":
            return None
        if pick.kind == "back":
            cursor.back()
            continue
        if pick.kind == "more":
            if not _next_window(cursor, sequence, pager.next_page, budget, line_cost):
                typer.echo(NO_MORE_ROWS)
                render = False
            continue
        return pick.summary


def tree_stage(
    client: HubClientProtocol, current: ModelSummary, trail: list[str]
) -> tuple[str, ModelSummary] | None:
    """Show one repo's tree a window at a time; return the pick or None.

    Returns:
        ``("pull", repo)`` or ``("navigate", repo)``. Grouping is NOT
        derived here (review adjudication 2026-07-13): the handoff
        passes ``model=None`` so pull's confirm-gated, format-directed
        default decides the canonical home — hub metadata never names
        an archive directory without a human yes.
    """
    parents = build_parent_chain(current.repo_id, current.base_model, client.model_summary)
    # Root first, matching the renderer's ancestry-ladder numbering.
    navigable = [link.summary for link in reversed(parents) if link.summary is not None]
    pagers = [
        (relation, client.list_children(current.repo_id, relation)) for relation in _RELATION_ORDER
    ]
    sequence = RowSequence(pinned_count=len(navigable))
    cursor = WindowCursor()
    width = resolve_window_width(sys.stdout)
    chrome = tree_chrome_lines(current, parents, trail, width=width)
    budget = resolve_window_size(sys.stdout, chrome)
    line_cost = row_line_cost(width)

    def fetch() -> list[ModelSummary]:
        """Advance every relation one page.

        A base with hundreds of quants must not make its finetunes
        unreachable (review adjudication 2026-07-13).
        """
        batch: list[ModelSummary] = []
        for _, pager in pagers:
            if not pager.exhausted:
                batch.extend(pager.next_page())
        return batch

    _next_window(cursor, sequence, fetch, budget, line_cost)
    render = True
    while True:
        more = cursor.end < len(sequence) or any(not pager.exhausted for _, pager in pagers)
        rows, footer = _visible(sequence, cursor, more_available=more)
        if render:
            begin_frame()
            for line in render_tree_page(
                current,
                parents,
                rows,
                footer=footer,
                trail=trail,
                section_continued=_continues_section(sequence, cursor),
            ):
                typer.echo(line)
        render = True
        options = [
            Option(key=str(number), kind="navigate", summary=summary)
            for number, summary in enumerate(navigable, start=1)
        ]
        options.extend(_offered_options(sequence, cursor))
        # "0" is the STABLE pull key (adjudicated 2026-07-13): the last
        # number shifted on every "m" page fetch.
        options.append(Option(key="0", kind="select", summary=current))
        page = DiscoveryPage(
            stage="tree",
            options=tuple(options),
            more_available=more,
            back_available=cursor.back_available,
        )
        hints = key_hints(more_available=more, back_available=cursor.back_available)
        prompt_text = clean_text(
            f"hop the tree by number — 0 = pull {current.repo_id} ({hints})",
            single_line=True,
        )
        pick = prompt_pick(page, prompt_text)
        if pick.kind == "quit":
            return None
        if pick.kind == "back":
            cursor.back()
            continue
        if pick.kind == "more":
            if not _next_window(cursor, sequence, fetch, budget, line_cost):
                typer.echo(NO_MORE_ROWS)
                render = False
            continue
        if pick.kind == "select" and pick.summary is not None:
            return ("pull", pick.summary)
        if pick.summary is not None:
            return ("navigate", pick.summary)
        # Unreachable: every numbered option carries a summary.
