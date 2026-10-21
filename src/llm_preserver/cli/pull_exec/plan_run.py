"""The ``pull --plan`` branch of the shared pull flow (spec 0005).

Extracted from ``flow.py`` (spec 0024) so the plan branch and the
pull branch each stay readable. The plan prepares through the same
code path a real pull executes, reports, and exits: confirmations are
recorded as would-ask lines, never asked.
"""

from pathlib import Path

import typer

from llm_preserver.cli.resume_hint import compose_resume_hint
from llm_preserver.hub import HubClientProtocol, RepoInfo
from llm_preserver.pull import validated_base_model, validated_roles
from llm_preserver.pull_preflight import require_disk_budget
from llm_preserver.pull_prepare import prepare_pull
from llm_preserver.pull_report import render_plan
from llm_preserver.render import clean_text


def run_plan(
    path: Path,
    repo_id: str,
    client: HubClientProtocol,
    info: RepoInfo | None,
    *,
    patterns: list[str],
    select_all: bool,
    roles: tuple[str, ...],
    base_model: str | None,
    refresh_docs: bool,
    emit_hint: bool,
    hf_logging: bool,
    requested_revision: str | None = None,
) -> None:
    """Prepare, report, and preflight a dry-run pull; write nothing.

    Args:
        path: An initialized archive root.
        repo_id: Exact hub repo id to plan.
        client: The hub seam.
        info: Pre-fetched metadata, or None to fetch in ``prepare_pull``.
        patterns: The selection patterns (typed or chosen interactively).
        select_all: Whole-tree snapshot mode.
        roles: Roles a real pull would assign (validated here).
        base_model: Curator-asserted lineage (validated here).
        refresh_docs: Plan replacements for changed doc files.
        emit_hint: Whether to print the follow-up pull command.
        hf_logging: Whether the follow-up command replays --hf-logging.
        requested_revision: The ``--revision`` value as typed, or None;
            when set, the report names it beside the resolved commit and
            the follow-up command pins that commit (spec 0024).

    Raises:
        PullError: Planning faults, mapped by the caller.
        PullPreflightError: Via ``require_disk_budget`` when the plan
            does not fit — after the report prints (exit 3).
    """
    # Roles validate here too: plan exit 0 must mean the real command
    # would proceed, and a bad --role would exit 2.
    validated_roles(roles)
    validated_base_model(base_model)
    would_ask: list[str] = []

    def record_prompt(prompt: str) -> bool:
        would_ask.append(clean_text(prompt, single_line=True))
        return True

    prep = prepare_pull(
        path,
        repo_id,
        client,
        include=patterns,
        repo_info=info,
        refresh_docs=refresh_docs,
        select_all=select_all,
        confirm=record_prompt,
        pin_archived_commit=requested_revision is not None,
    )
    for line in render_plan(prep, would_ask, requested_revision=requested_revision):
        if line.startswith("warning:"):
            # Likely human error —
            # highlight it; click strips color off-terminal.
            typer.secho(line, fg=typer.colors.YELLOW, bold=True)
        else:
            typer.echo(line)
    if emit_hint:
        # No --model: plan mode recorded the confirmations instead of
        # asking; the destination is the typed repo and the hint must
        # not bake one in (0006 adjudication). No --plan: the follow-up
        # wanted is the real pull.
        plan_hint = compose_resume_hint(
            repo_id,
            path,
            include=patterns,
            select_all=select_all,
            roles=roles,
            base_model=base_model,
            refresh_docs=refresh_docs,
            hf_logging=hf_logging,
            revision=None if requested_revision is None else prep.info.commit,
        )
        if plan_hint is not None:
            typer.echo(plan_hint)
    # After the report, so an over-budget plan still prints before
    # refusing (exit 3, scripts gate on it).
    require_disk_budget(path, prep.needed_bytes, prep.disk_free)
