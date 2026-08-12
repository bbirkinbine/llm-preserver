"""Pure renderers for discovery listings (spec 0006, windowed by 0015): data → lines.

Rows carry hub facts only — downloads, dates, gated markers — never a
score or ranking of the tool's own. Every line passes through
``clean_text``: discovery output is 100% hub-supplied text, and
terminal control characters must never reach a terminal raw.

Spec 0015 made a page a *window* rather than the whole accumulated
listing: rows arrive already numbered (``NumberedRow``, numbered once
at fetch time so a number never renames a repo), the renderer prints
the slice it is handed, and a footer says where that slice sits.
"""

from collections.abc import Callable, Sequence

from llm_preserver.discover import ParentLink
from llm_preserver.discover_paging import NumberedRow, WindowFooter
from llm_preserver.hub_discovery import ModelSummary
from llm_preserver.render import clean_text
from llm_preserver.text_window import wrapped_height

# Lines a tree frame spends on something other than the lines counted
# explicitly by ``tree_chrome_lines``: the frame separator rule and its
# blank line. Budgeting for them is what keeps a frame inside one screen.
_FRAME_RESERVE = 2

_DERIVATIVES_LABEL = (
    "down — derivatives of this repo (hub-sorted by downloads; picking drills into one):"
)


def _line(text: str) -> str:
    """Sanitize one output line (hub text is untrusted)."""
    return clean_text(text, single_line=True)


def summary_facts(summary: ModelSummary) -> str:
    """Render a row's hub facts; absent facts are omitted, never None."""
    parts = []
    if summary.downloads is not None:
        parts.append(f"{summary.downloads} downloads")
    if summary.last_modified is not None:
        parts.append(summary.last_modified[:10])
    if summary.gated is not None:
        parts.append("gated")
    return f"  —  {' · '.join(parts)}" if parts else ""


def key_hints(*, more_available: bool, back_available: bool) -> str:
    """List the keys this frame offers, quit last.

    Only offered keys appear: naming ``b`` on the first frame, where
    there is nothing to go back to, would advertise a rejected pick.
    ``b`` says "back a page" rather than bare "back" — the tree stage
    already means something by going back (the ``your path:`` trail
    pops when you hop to a repo you came from), and a user typing ``b``
    to leave a repo must not silently get a previous window instead
    (review round, 2026-08-06).
    """
    parts = []
    if more_available:
        parts.append("m = more")
    if back_available:
        parts.append("b = back a page")
    parts.append("q = quit")
    return ", ".join(parts)


def row_line(row: NumberedRow) -> str:
    """Render one listing row — the single source of the row format.

    Both renderers and the window-budget cost read this, so a frame can
    never be sized against a shape different from the one it prints.
    """
    return _line(f"  {row.number}. {row.summary.repo_id}{summary_facts(row.summary)}")


def row_line_cost(width: int | None) -> Callable[[NumberedRow], int]:
    """Build the per-row line cost ``fit_rows`` charges at ``width``.

    Args:
        width: Terminal columns, or None for a piped run.

    Returns:
        A callable giving each row its wrapped height — always 1 when
        the width is unknown, which keeps piped output byte-identical.
    """

    def cost(row: NumberedRow) -> int:
        return wrapped_height(row_line(row), width)

    return cost


def render_footer(footer: WindowFooter) -> str:
    """Say which picks are on screen, out of how many exist, and what works.

    ``highest`` grows as you page — it is the count of numbers handed
    out so far, not a total. The hub publishes no total.
    """
    text = f"showing {footer.first}-{footer.last} of {footer.highest}"
    if footer.more_available:
        text += " — more (m)"
    if footer.back_available:
        text += " · back (b)"
    return text


def render_search_page(
    rows: Sequence[NumberedRow], *, query: str, footer: WindowFooter | None
) -> list[str]:
    """Render one window of search results, the hub's order verbatim.

    Args:
        rows: This window's rows, already numbered, in hub order.
        query: The free-text query, echoed in the header.
        footer: Position line for the window; None renders no footer.

    Returns:
        Printable lines: header, numbered rows, optional footer.
    """
    lines = [_line(f"hub search results for '{query}' (the hub's relevance order):")]
    lines.extend(row_line(row) for row in rows)
    if footer is not None:
        lines.append(render_footer(footer))
    return lines


def _ancestry_lines(current: ModelSummary, parents: Sequence[ParentLink]) -> list[str]:
    """Render the upward chain as a ladder, root at top.

    Lineage direction is visible structure, not a caption (live-use
    feedback 2026-07-13: a flat "nearest first" list read as a ranked
    menu, and which end was the root was guesswork).

    IMPORTANT ordering contract: numbered ancestry options render ROOT
    FIRST — callers building pick options must number the navigable
    parents in reversed ``build_parent_chain`` order. A not-found
    parent is shown but takes no number (it cannot be navigated into).
    """
    if not parents:
        return []
    lines = ["up — ancestry, root at top (picking a number climbs the tree):"]
    number = 1
    depth = 0
    for index, link in enumerate(reversed(parents)):
        branch = f"{'   ' * depth}{'└─ ' if depth else ''}"
        is_topmost = index == 0
        if link.status == "not-found":
            lines.append(
                _line(f"      {branch}{link.requested_id} — not found on the hub (stale metadata)")
            )
            depth += 1
            continue
        if link.summary is None:
            continue
        root_tag = (
            "  [original — no parent]" if is_topmost and link.summary.base_model is None else ""
        )
        if link.status == "renamed":
            entry = f"{link.requested_id} — renamed, now {link.summary.repo_id}"
        else:
            entry = link.summary.repo_id
        lines.append(_line(f"  {number}. {branch}{entry}{summary_facts(link.summary)}{root_tag}"))
        number += 1
        depth += 1
    branch = f"{'   ' * depth}└─ "
    lines.append(_line(f"      {branch}{current.repo_id}  [this repo — you are here]"))
    return lines


def tree_chrome_lines(
    current: ModelSummary,
    parents: Sequence[ParentLink],
    trail: Sequence[str],
    *,
    width: int | None = None,
) -> int:
    """Count the lines a tree frame spends on everything but child rows.

    The window budget is the terminal height less this, so the count
    must come from the same code that renders the chrome — a
    hand-maintained constant would drift the first time the ladder
    changed shape. Each chrome line is charged its *wrapped* height:
    the breadcrumb in particular is unbounded (nothing caps trail
    length the way ``MAX_PARENT_HOPS`` caps the ladder), and a six-hop
    trail measured 444 characters — six physical lines charged as one
    before the review round caught it (2026-08-06).

    Args:
        current: The repo whose tree is shown.
        parents: Its upward chain.
        trail: The navigation breadcrumb.
        width: Terminal columns, or None to charge one line each.

    Returns:
        Header, breadcrumb, ancestry ladder, the "down —" label, the
        pull-this-repo line, the footer, the prompt, and the reserve.
    """
    texts = [_line(f"model tree for {current.repo_id}:")]
    if len(trail) > 1:
        texts.append(_line(f"your path: {' → '.join(trail)}  (you are here)"))
    texts.extend(_ancestry_lines(current, parents))
    texts.append(_DERIVATIVES_LABEL)
    texts.append(_line(f"  0. pull this repo ({current.repo_id})"))
    # The footer and prompt at their widest shape — both keys offered —
    # so a frame is never sized against a narrower variant than it prints.
    texts.append("showing 000-000 of 000 — more (m) · back (b)")
    texts.append(
        _line(
            f"hop the tree by number — 0 = pull {current.repo_id} "
            f"({key_hints(more_available=True, back_available=True)})"
        )
    )
    return sum(wrapped_height(text, width) for text in texts) + _FRAME_RESERVE


def render_tree_page(
    current: ModelSummary,
    parents: Sequence[ParentLink],
    children: Sequence[NumberedRow],
    *,
    footer: WindowFooter | None,
    trail: Sequence[str] = (),
    section_continued: bool = False,
) -> list[str]:
    """Render one model-tree window: ancestry ladder, children, one pull.

    Every frame reprints the header, the ladder and the pull line, so a
    window is actionable without scrollback — the terminals this spec
    was written for may have none.

    Args:
        current: The repo whose tree is shown.
        parents: Upward chain, nearest first (``build_parent_chain``).
        children: This window's derivative rows, already numbered.
        footer: Position line; None renders no footer.
        trail: Repo ids navigated through to get here, oldest first.
        section_continued: True when this window's first row continues
            the section the previous window ended in, which labels it
            "(continued)" rather than repeating it as if it were new.

    Returns:
        Printable lines ending with the numbered "pull this repo"
        option and the footer.
    """
    lines = [_line(f"model tree for {current.repo_id}:")]
    if len(trail) > 1:
        lines.append(_line(f"your path: {' → '.join(trail)}  (you are here)"))
    lines.extend(_ancestry_lines(current, parents))
    if children:
        lines.append(_DERIVATIVES_LABEL)
    previous_relation: str | None = None
    for index, row in enumerate(children):
        relation = row.summary.relation
        if relation is not None and (index == 0 or relation != previous_relation):
            suffix = " (continued):" if index == 0 and section_continued else ":"
            lines.append(_line(f"{relation} versions{suffix}"))
        previous_relation = relation
        lines.append(row_line(row))
    lines.append(_line(f"  0. pull this repo ({current.repo_id})"))
    if footer is not None:
        lines.append(render_footer(footer))
    return lines
