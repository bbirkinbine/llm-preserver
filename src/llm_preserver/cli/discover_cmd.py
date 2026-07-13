"""The discover command: deterministic name-to-pull navigation.

Spec 0006: hub free-text search (the hub's order, verbatim) → typed
model-tree listing (parents up, children down) → the unmodified pull
flow via the shared core, with the canonical model directory derived
from the navigated tree. Every step is a numbered human pick; the
tool never ranks, never selects, never pulls on its own.
"""

from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError, require_archive
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.cli.pull_exec import (
    exit_for_pull_error,
    make_hub_client,
    run_pull,
    setup_logging,
)
from llm_preserver.discover import DiscoveryPage, Option, build_parent_chain, parse_pick
from llm_preserver.discover_render import render_search_page, render_tree_page
from llm_preserver.hub import HubClientProtocol, PullError, PullUserError
from llm_preserver.hub_discovery import ModelSummary, RelationType
from llm_preserver.render import clean_text

# Fixed relation order for tree children (spec 0006): deterministic,
# and the most preservation-relevant group (quantized) lists first.
_RELATION_ORDER: tuple[RelationType, ...] = ("quantized", "finetune", "adapter", "merge")

# Frame separator: accumulate-paging re-renders whole listings, so
# scrollback needs an unmissable boundary marking where the current
# frame starts (live-use feedback 2026-07-13).
_FRAME_RULE = "─" * 72


def _begin_frame() -> None:
    """Mark the start of a freshly rendered listing in the scrollback."""
    typer.echo("")
    typer.echo(_FRAME_RULE)


# Consecutive invalid inputs before refusing: a human mistypes once or
# twice; an endless invalid stream is a pipe, and discovery is
# interactive-only (spec non-goal) — refuse deterministically instead
# of livelooping (review adjudication 2026-07-13).
_MAX_INVALID_PICKS = 5


def _prompt_pick(page: DiscoveryPage, prompt_text: str) -> Option:
    """Prompt until the input is a valid pick; EOF quits cleanly.

    Discovery is inherently interactive (scripted discovery is a spec
    non-goal), so exhausted stdin is treated as quitting, not an
    error — and a never-ending invalid stream is refused after a few
    tries rather than looping forever. The prompt text is
    stage-supplied: discovery is open-ended graph browsing, not a
    fixed-step wizard, so each prompt must say what the stage is and
    name the pick that ends it (live-use feedback 2026-07-13 — a
    long listing buried the "pull this repo" line off-screen).
    """
    invalid = 0
    while True:
        try:
            raw = typer.prompt(prompt_text)
        except typer.Abort:
            return Option(key="q", kind="quit", summary=None)
        pick = parse_pick(raw, page)
        if pick is not None:
            return pick
        invalid += 1
        if invalid >= _MAX_INVALID_PICKS:
            raise PullUserError(
                f"{_MAX_INVALID_PICKS} invalid picks in a row: discover needs an "
                "interactive terminal — scripts should use pull with exact repo ids"
            )
        typer.echo("not a listed pick — enter a listed number, m, or q")


def _search_stage(client: HubClientProtocol, query: str) -> ModelSummary | None:
    """Run the search stage; return the picked repo or None to quit."""
    pager = client.search_models(query)
    rows = pager.next_page()
    if not rows:
        message = f"no hub results for '{query}' — refine the query and re-run"
        typer.echo(clean_text(message, single_line=True))
        return None
    while True:
        _begin_frame()
        for line in render_search_page(
            rows, fetched=pager.fetched, exhausted=pager.exhausted, query=query
        ):
            typer.echo(line)
        page = DiscoveryPage(
            stage="search",
            options=tuple(
                Option(key=str(number), kind="navigate", summary=row)
                for number, row in enumerate(rows, start=1)
            ),
            more_available=not pager.exhausted,
        )
        pick = _prompt_pick(page, "pick a model to explore (number; m = more, q = quit)")
        if pick.kind == "quit":
            return None
        if pick.kind == "more":
            rows = rows + pager.next_page()
            continue
        return pick.summary


def _tree_stage(
    client: HubClientProtocol, current: ModelSummary, trail: list[str]
) -> tuple[str, ModelSummary] | None:
    """Show one repo's tree; return the pick or None to quit.

    Returns:
        ``("pull", repo)`` or ``("navigate", repo)``. Grouping is NOT
        derived here (review adjudication 2026-07-13): the handoff
        passes ``model=None`` so pull's confirm-gated, format-directed
        default decides the canonical home — hub metadata never names
        an archive directory without a human yes.
    """
    parents = build_parent_chain(current.repo_id, current.base_model, client.model_summary)
    pagers = [
        (relation, client.list_children(current.repo_id, relation)) for relation in _RELATION_ORDER
    ]
    children: list[ModelSummary] = []
    for _, pager in pagers:
        children.extend(pager.next_page())
    relation_rank: dict[str, int] = {
        relation: rank for rank, relation in enumerate(_RELATION_ORDER)
    }
    while True:
        # Stable re-group after paging: a later "m" page must join its
        # relation's section, not trail the list under a duplicate
        # header (hub order is preserved within each relation).
        children.sort(key=lambda child: relation_rank.get(child.relation or "", len(relation_rank)))
        more_available = any(not pager.exhausted for _, pager in pagers)
        _begin_frame()
        for line in render_tree_page(
            current, parents, children, more_available=more_available, trail=trail
        ):
            typer.echo(line)
        # Root first, matching the renderer's ancestry-ladder numbering.
        navigable = [link.summary for link in reversed(parents) if link.summary is not None]
        numbered: list[Option] = []
        for target in [*navigable, *children]:
            numbered.append(Option(key=str(len(numbered) + 1), kind="navigate", summary=target))
        # "0" is the STABLE pull key (adjudicated 2026-07-13): the last
        # number shifted on every "m" page fetch.
        numbered.append(Option(key="0", kind="select", summary=current))
        page = DiscoveryPage(stage="tree", options=tuple(numbered), more_available=more_available)
        prompt_text = clean_text(
            f"hop the tree by number — 0 = pull {current.repo_id} (m = more, q = quit)",
            single_line=True,
        )
        pick = _prompt_pick(page, prompt_text)
        if pick.kind == "quit":
            return None
        if pick.kind == "more":
            # Advance every relation one page: a base with hundreds of
            # quants must not make its finetunes unreachable
            # (review adjudication 2026-07-13).
            for _, pager in pagers:
                if not pager.exhausted:
                    children.extend(pager.next_page())
            continue
        if pick.kind == "select" and pick.summary is not None:
            return ("pull", pick.summary)
        if pick.summary is not None:
            return ("navigate", pick.summary)
        # Unreachable: every numbered option carries a summary.


def _prompt_archive_mode() -> bool | None:
    """Ask how to archive the selected repo; None means quit.

    Live-use gap (2026-07-13): discover only handed off into
    pick-files mode, marching users through a 56-file listing when
    the repo was an original whose whole tree is the artifact
    (spec 0004). Returns True for a whole-repo snapshot, False for
    selective picking.
    """
    invalid = 0
    while True:
        try:
            raw = typer.prompt(
                "archive how? 1 = pick files, 2 = whole-repo snapshot "
                "(originals/masters: usually 2; quant repos: usually 1; q = quit)"
            )
        except typer.Abort:
            return None
        text = raw.strip().lower()
        if text == "q":
            return None
        if text == "1":
            return False
        if text == "2":
            return True
        invalid += 1
        if invalid >= _MAX_INVALID_PICKS:
            raise PullUserError(
                f"{_MAX_INVALID_PICKS} invalid picks in a row: discover needs an "
                "interactive terminal — scripts should use pull with exact repo ids"
            )
        typer.echo("enter 1, 2, or q")


def _run_discovery(path: Path, client: HubClientProtocol, query: str, plan: bool) -> None:
    """Drive search → tree hops → the shared pull core.

    The trail is a stack of visited repo ids: hopping to a repo
    already on it pops back to that point (live-use feedback
    2026-07-13 — "what have I stacked vs. what am I picking next").
    """
    current = _search_stage(client, query)
    trail: list[str] = [] if current is None else [current.repo_id]
    while current is not None:
        outcome = _tree_stage(client, current, trail)
        if outcome is None:
            return
        kind, target = outcome
        if kind == "navigate":
            current = target
            if target.repo_id in trail:
                del trail[trail.index(target.repo_id) + 1 :]
            else:
                trail.append(target.repo_id)
            continue
        whole_repo = _prompt_archive_mode()
        if whole_repo is None:
            return
        # One metadata call per pull (spec 0003): fetched here, shared
        # with the file listing and the pull via run_pull's seam.
        # model=None on purpose (review adjudication 2026-07-13): the
        # pull's own confirm-gated, format-directed grouping runs —
        # exactly as if the user had typed the repo id themselves.
        info = client.repo_info(target.repo_id)
        run_pull(
            path,
            target.repo_id,
            client,
            include=[],
            select_all=whole_repo,
            plan=plan,
            repo_info=info,
            # The discover invocation is what shell history holds; the
            # resume hint is the only record of the pull shape (0007).
            resume_hint=True,
        )
        return


@app.command()
def discover(
    query: Annotated[str, typer.Argument(help="Free-text hub search (the hub's own results).")],
    # Path comes LAST: Click binds positionals left-to-right, so the
    # env-var fallback only works when the omittable argument trails.
    path: ArchivePath,
    plan: Annotated[
        bool,
        typer.Option(
            "--plan",
            help="Dry run the final pull: print what it would do, write nothing.",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show per-file progress and client detail.")
    ] = False,
) -> None:
    """Find a model by name and pull it — search, model tree, pull, no browser."""
    setup_logging(verbose)
    # Fail fast on a bad archive path — before any network call.
    try:
        require_archive(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    client = make_hub_client()
    try:
        _run_discovery(path, client, query, plan)
    except PullError as exc:
        raise exit_for_pull_error(exc) from exc
