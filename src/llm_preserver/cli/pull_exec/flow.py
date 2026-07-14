"""The shared pull-execution flow: interactive listing, plan, or pull.

Extracted from ``pull_cmd`` (spec 0006): the ``discover`` handoff must
land in exactly the pull flow the ``pull`` command runs — same
interactive listing, confirmations, dry-run branch, and fault-domain
exit mapping — so that flow lives once, parameterized with an
optional pre-fetched ``RepoInfo`` (one metadata call per pull, spec
0003).
"""

from pathlib import Path

import typer

from llm_preserver.archive import ArchiveError
from llm_preserver.cli.app import fail
from llm_preserver.cli.pull_exec.plumbing import exit_for_pull_error
from llm_preserver.cli.pull_exec.prompts import confirm_or_stop, prompt_for_selection
from llm_preserver.cli.resume_hint import compose_resume_hint
from llm_preserver.hub import HubClientProtocol, PullError, RepoInfo
from llm_preserver.pull import pull_model, validated_roles
from llm_preserver.pull_preflight import require_disk_budget
from llm_preserver.pull_prepare import prepare_pull
from llm_preserver.pull_report import render_plan
from llm_preserver.render import clean_text


def run_pull(
    path: Path,
    repo_id: str,
    client: HubClientProtocol,
    *,
    include: list[str],
    select_all: bool = False,
    model: str | None = None,
    roles: tuple[str, ...] = (),
    refresh_docs: bool = False,
    plan: bool = False,
    yes: bool = False,
    repo_info: RepoInfo | None = None,
    resume_hint: bool = False,
    hf_logging: bool = False,
) -> None:
    """Run the pull flow: interactive listing, plan or pull, exit mapping.

    Args:
        path: An initialized archive root (caller has verified it).
        repo_id: Exact hub repo id to pull from.
        client: The hub seam (real client or a test double).
        include: fnmatch patterns; empty (without ``select_all``)
            triggers the interactive file listing.
        select_all: Whole-tree snapshot mode (``--whole-repo``).
        model: Canonical model directory override.
        roles: Roles to assign at pull time.
        refresh_docs: Replace changed upstream doc files.
        plan: Dry run (spec 0005) — report and exit, write nothing.
        yes: Auto-accept the size confirmation.
        repo_info: Pre-fetched metadata (the discover handoff) — spec
            0003's one metadata call per pull; None fetches here.
        resume_hint: Force the resume-command hint's transfer-start
            print (spec 0007). The discover handoff passes True; a
            direct pull earns that print only when the interactive
            file listing shaped it — a user-typed shape is already in
            shell history. Ctrl-C prints the hint regardless: an
            interrupted transfer always ends with the continue line.
        hf_logging: Whether --hf-logging is in effect this run; the
            composed hint replays it (spec 0008 — the stalled-transfer
            scenario the hint serves is the one the flag exists for).

    Raises:
        typer.Exit: The fault-domain exit for any pull failure, or
            exit 130 when Ctrl-C interrupts the transfer.
    """
    # Spec 0007: a hint is owed whenever the pull's shape was assembled
    # interactively; hint holds the printed line for the Ctrl-C repeat.
    emit_hint = resume_hint or (not select_all and not include)
    hint: str | None = None
    try:
        patterns = list(include)
        info = repo_info
        if not select_all and not patterns:
            if info is None:
                info = client.repo_info(repo_id)
            patterns = prompt_for_selection(info, repo_id)
        if plan:
            # Dry run (spec 0005): prepare through the same code path a
            # real pull executes, report, and exit — confirmations are
            # recorded as would-ask lines, never asked. The preflight
            # check runs after the report so an over-budget plan still
            # prints before refusing (exit 3, scripts gate on it).
            # Roles validate here too: plan exit 0 must mean the real
            # command would proceed, and a bad --role would exit 2.
            validated_roles(roles)
            would_ask: list[str] = []

            def record_prompt(prompt: str) -> bool:
                would_ask.append(clean_text(prompt, single_line=True))
                return True

            prep = prepare_pull(
                path,
                repo_id,
                client,
                include=patterns,
                model=model,
                repo_info=info,
                refresh_docs=refresh_docs,
                select_all=select_all,
                confirm=record_prompt,
            )
            for line in render_plan(prep, would_ask):
                if line.startswith("warning:"):
                    # Likely human error (e.g. grouping mismatch) —
                    # highlight it; click strips color off-terminal.
                    typer.secho(line, fg=typer.colors.YELLOW, bold=True)
                else:
                    typer.echo(line)
            if emit_hint:
                # No --model: plan mode recorded the confirmations
                # instead of asking, so no grouping was human-confirmed
                # and the hint must not bake one in (0006 adjudication).
                # No --plan: the follow-up wanted is the real pull.
                plan_hint = compose_resume_hint(
                    repo_id,
                    path,
                    include=patterns,
                    select_all=select_all,
                    roles=roles,
                    refresh_docs=refresh_docs,
                    hf_logging=hf_logging,
                )
                if plan_hint is not None:
                    typer.echo(plan_hint)
            require_disk_budget(path, prep.needed_bytes, prep.disk_free)
            return

        def capture_resume_hint(resolved_model: str) -> None:
            # Runs after every confirmation, before the first byte
            # (pull_model's on_transfer_start seam). resolved_model is
            # the grouping the human just confirmed — replaying it as
            # --model keeps the continue in the same model directory.
            # The scrollback print is owed only to interactively
            # shaped pulls (a user-typed shape is in shell history),
            # but the hint is captured on EVERY pull: Ctrl-C always
            # ends with the continue line (live-use adjudication
            # 2026-07-13 — a resumed pull's second interrupt printed
            # nothing, and silence read as a miss). None means the
            # repo id failed hint validation; no hint beats a
            # booby-trapped one, and the pull itself proceeds.
            nonlocal hint
            hint = compose_resume_hint(
                repo_id,
                path,
                include=patterns,
                select_all=select_all,
                model=resolved_model,
                roles=roles,
                refresh_docs=refresh_docs,
                hf_logging=hf_logging,
            )
            if hint is not None and emit_hint:
                typer.echo(hint)

        model_dir = pull_model(
            path,
            repo_id,
            client,
            include=patterns,
            model=model,
            roles=roles,
            repo_info=info,
            refresh_docs=refresh_docs,
            select_all=select_all,
            # confirm_or_stop sanitizes hub-supplied prompt text and
            # converts unanswerable prompts to deterministic exits.
            confirm=lambda prompt: confirm_or_stop(prompt, yes),
            on_transfer_start=capture_resume_hint,
        )
    except KeyboardInterrupt:
        # Ctrl-C mid-transfer (spec 0007): repeat the hint as the final
        # line — directly above the next shell prompt — then exit
        # 128 + SIGINT. Never swallowed into a retry.
        if hint is not None:
            typer.echo(hint)
        raise typer.Exit(code=130) from None
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    except PullError as exc:
        raise exit_for_pull_error(exc) from exc
    typer.echo(clean_text(f"pulled {repo_id} into {model_dir}", single_line=True))
