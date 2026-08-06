"""Discovery orchestration: search, then tree hops, then the pull handoff.

Spec 0006: hub free-text search (the hub's order, verbatim) → typed
model-tree listing (parents up, children down) → the unmodified pull
flow via the shared core, with the canonical model directory derived
from the navigated tree. Every step is a numbered human pick; the
tool never ranks, never selects, never pulls on its own.

The stages themselves live in ``stages`` and their prompt plumbing in
``prompts`` — split out when spec 0015's windowed paging pushed this
module past the 300-line rule.
"""

from pathlib import Path

from llm_preserver.cli.discover_cmd.prompts import prompt_archive_mode
from llm_preserver.cli.discover_cmd.stages import search_stage, tree_stage
from llm_preserver.cli.pull_exec import run_pull
from llm_preserver.hub import HubClientProtocol


def run_discovery(
    path: Path, client: HubClientProtocol, query: str, plan: bool, hf_logging: bool = False
) -> None:
    """Drive search → tree hops → the shared pull core.

    The trail is a stack of visited repo ids: hopping to a repo
    already on it pops back to that point (live-use feedback
    2026-07-13 — "what have I stacked vs. what am I picking next").

    Args:
        path: The archive root the pull lands in.
        client: The hub seam.
        query: The free-text search term.
        plan: True to make the final pull a dry run (spec 0005).
        hf_logging: Passthrough of the vendor-telemetry flag (0008).
    """
    current = search_stage(client, query)
    trail: list[str] = [] if current is None else [current.repo_id]
    while current is not None:
        outcome = tree_stage(client, current, trail)
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
        whole_repo = prompt_archive_mode()
        if whole_repo is None:
            return
        # One metadata call per pull (spec 0003): fetched here, shared
        # with the file listing and the pull via run_pull's seam.
        # model=None on purpose (review adjudication 2026-07-13): the
        # pull's own confirm-gated grouping runs — exactly as if the
        # user had typed the repo id themselves.
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
            hf_logging=hf_logging,
        )
        return
