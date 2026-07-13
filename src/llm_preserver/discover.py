"""Discovery navigation state machine (spec 0006): pure, zero I/O.

The CLI renders pages and reads input; everything decision-shaped
lives here as data-in/data-out functions so the whole flow is
testable without a TTY. The tool never picks — ``parse_pick`` only
validates what the human typed against what the page offered.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from llm_preserver.hub import PullUserError
from llm_preserver.hub_discovery import ModelSummary, looks_like_repo_id

Stage = Literal["search", "tree"]

# Real lineage chains are short; a deeper one is hostile or broken
# metadata, and every hop is a network call fired on tree entry.
MAX_PARENT_HOPS = 10


@dataclass(frozen=True)
class Option:
    """One pick a page offers.

    Attributes:
        key: What the user types — "1".."N", "m", or "q".
        kind: What picking it means: navigate into a repo's tree,
            select the repo for pull, fetch more rows, or quit.
        summary: The target repo for navigate/select; None otherwise.
    """

    key: str
    kind: Literal["navigate", "select", "more", "quit"]
    summary: ModelSummary | None


@dataclass(frozen=True)
class DiscoveryPage:
    """What one prompt cycle shows and accepts.

    Attributes:
        stage: Which discovery stage the page belongs to.
        options: The numbered picks, keys "1".."N" in display order.
        more_available: True when "m" is a valid pick (the hub has
            more rows).
    """

    stage: Stage
    options: tuple[Option, ...]
    more_available: bool


def parse_pick(raw: str, page: DiscoveryPage) -> Option | None:
    """Validate one line of user input against a page.

    Args:
        raw: The line as typed.
        page: The page the input answers.

    Returns:
        The picked option — "q" always quits, "m" pages only when
        more rows are available, a digit picks its numbered option —
        or None for anything invalid (empty, zero, out of range,
        garbage).
    """
    text = raw.strip().lower()
    if text == "q":
        return Option(key="q", kind="quit", summary=None)
    if text == "m":
        return Option(key="m", kind="more", summary=None) if page.more_available else None
    # Exact key match — options carry their own keys ("1".."N", and the
    # tree's stable "0" = pull-this-repo, adjudicated 2026-07-13: the
    # last number shifted on every page fetch). "01"-style aliases stay
    # invalid; keys are matched verbatim after strip/lowercase.
    for option in page.options:
        if text == option.key:
            return option
    return None


@dataclass(frozen=True)
class ParentLink:
    """One hop of a repo's upward ``base_model`` chain.

    Attributes:
        requested_id: The declared parent id the hop asked for.
        summary: The fetched summary; None exactly when not-found.
        status: "ok" (fetched under the requested id), "renamed" (the
            hub redirected to a new id — shown, never auto-followed
            silently), or "not-found" (stale metadata; chain stops).
    """

    requested_id: str
    summary: ModelSummary | None
    status: Literal["ok", "not-found", "renamed"]


def build_parent_chain(
    repo_id: str,
    base_model: str | None,
    fetch: Callable[[str], ModelSummary],
) -> list[ParentLink]:
    """Walk a repo's declared parents upward, nearest first.

    Stale metadata is presented honestly (spec 0006): a missing
    parent becomes a "not-found" link and stops the walk; a renamed
    parent is marked and the walk continues from the hub's current
    id. A seen-set over the starting repo plus every requested and
    returned id guards against metadata cycles.

    Args:
        repo_id: The repo whose chain is being built.
        base_model: Its declared parent, or None.
        fetch: Light summary lookup; raises the user-input fault
            (the 404 mapping) for a missing repo.

    Returns:
        The chain, nearest parent first; empty when none declared.
    """
    chain: list[ParentLink] = []
    seen = {repo_id}
    next_id = base_model
    while next_id and next_id not in seen and len(chain) < MAX_PARENT_HOPS:
        seen.add(next_id)
        if not looks_like_repo_id(next_id):
            # Malformed metadata never becomes a request parameter;
            # shown like a stale parent — unusable either way.
            chain.append(ParentLink(requested_id=next_id, summary=None, status="not-found"))
            break
        try:
            parent = fetch(next_id)
        except PullUserError:
            chain.append(ParentLink(requested_id=next_id, summary=None, status="not-found"))
            break
        status: Literal["ok", "renamed"] = "ok" if parent.repo_id == next_id else "renamed"
        chain.append(ParentLink(requested_id=next_id, summary=parent, status=status))
        seen.add(parent.repo_id)
        next_id = parent.base_model
    return chain
