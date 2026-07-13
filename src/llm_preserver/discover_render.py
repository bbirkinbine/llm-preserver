"""Pure renderers for discovery listings (spec 0006): data → lines.

Rows carry hub facts only — downloads, dates, gated markers — never a
score or ranking of the tool's own. Every line passes through
``clean_text``: discovery output is 100% hub-supplied text, and
terminal control characters must never reach a terminal raw.
"""

from collections.abc import Sequence

from llm_preserver.discover import ParentLink
from llm_preserver.hub_discovery import ModelSummary
from llm_preserver.render import clean_text


def _line(text: str) -> str:
    """Sanitize one output line (hub text is untrusted)."""
    return clean_text(text, single_line=True)


def _facts(summary: ModelSummary) -> str:
    """Render a row's hub facts; absent facts are omitted, never None."""
    parts = []
    if summary.downloads is not None:
        parts.append(f"{summary.downloads} downloads")
    if summary.last_modified is not None:
        parts.append(summary.last_modified[:10])
    if summary.gated is not None:
        parts.append("gated")
    return f"  —  {' · '.join(parts)}" if parts else ""


def render_search_page(
    rows: Sequence[ModelSummary], *, fetched: int, exhausted: bool, query: str
) -> list[str]:
    """Render one page of search results, the hub's order verbatim.

    Args:
        rows: This page's rows, in the order the hub returned them.
        fetched: Total rows fetched so far (across pages).
        exhausted: True when the hub has no further rows.
        query: The free-text query, echoed in the header.

    Returns:
        Printable lines: header, numbered rows, and a
        "more available" footer only while the hub has more.
    """
    lines = [_line(f"hub search results for '{query}' (the hub's relevance order):")]
    lines.extend(
        _line(f"  {number}. {row.repo_id}{_facts(row)}") for number, row in enumerate(rows, start=1)
    )
    if not exhausted:
        lines.append(f"showing {fetched} — more available (m)")
    return lines


def render_tree_page(
    current: ModelSummary,
    parents: Sequence[ParentLink],
    children: Sequence[ModelSummary],
    *,
    more_available: bool,
    trail: Sequence[str] = (),
) -> list[str]:
    """Render one model-tree page: ancestry ladder, children, one pull.

    The ancestry renders as a ladder — root at the top, indenting
    down to the current repo — so lineage direction is visible
    structure, not a caption (live-use feedback 2026-07-13: a flat
    "nearest first" list read as a ranked menu, and which end was
    the root was guesswork). Numbering is continuous across sections
    so pick numbers are unambiguous; a not-found parent is shown but
    takes no number (it cannot be navigated into).

    IMPORTANT ordering contract: numbered ancestry options render
    ROOT FIRST — callers building pick options must number the
    navigable parents in reversed ``build_parent_chain`` order.

    Args:
        current: The repo whose tree is shown.
        parents: Upward chain, nearest first (``build_parent_chain``).
        children: Derivative rows, pre-grouped by relation, hub order.
        more_available: True when the children listing has more rows.
        trail: Repo ids navigated through to get here, oldest first
            (the current repo last).

    Returns:
        Printable lines ending with the numbered "pull this repo"
        option (and the more-available footer when applicable).
    """
    lines = [_line(f"model tree for {current.repo_id}:")]
    if len(trail) > 1:
        lines.append(_line(f"your path: {' → '.join(trail)}  (you are here)"))
    number = 1
    if parents:
        lines.append("up — ancestry, root at top (picking a number climbs the tree):")
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
        lines.append(_line(f"  {number}. {branch}{entry}{_facts(link.summary)}{root_tag}"))
        number += 1
        depth += 1
    if parents:
        branch = f"{'   ' * depth}└─ "
        lines.append(_line(f"      {branch}{current.repo_id}  [this repo — you are here]"))
    previous_relation = None
    if children:
        lines.append(
            "down — derivatives of this repo (hub-sorted by downloads; picking drills into one):"
        )
    for child in children:
        if child.relation != previous_relation:
            lines.append(_line(f"{child.relation or 'related'} versions:"))
            previous_relation = child.relation
        lines.append(_line(f"  {number}. {child.repo_id}{_facts(child)}"))
        number += 1
    lines.append(_line(f"  0. pull this repo ({current.repo_id})"))
    if more_available:
        lines.append(f"showing {len(children)} children — more available (m)")
    return lines
