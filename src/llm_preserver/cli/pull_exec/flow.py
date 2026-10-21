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
from llm_preserver.archive_status import picker_status
from llm_preserver.cli.app import fail
from llm_preserver.cli.pull_exec.confirmations import confirm_or_stop
from llm_preserver.cli.pull_exec.plan_run import run_plan
from llm_preserver.cli.pull_exec.plumbing import exit_for_pull_error
from llm_preserver.cli.pull_exec.prompts import prompt_for_selection
from llm_preserver.cli.resume_hint import compose_doc_refresh_hint, compose_resume_hint
from llm_preserver.hub import (
    HubClientProtocol,
    PullDocRefreshError,
    PullError,
    PullInvalidIdError,
    RepoInfo,
)
from llm_preserver.layout import UnmigratedArchiveError
from llm_preserver.ollama_store import ollama_shape_hint
from llm_preserver.pull import pull_model
from llm_preserver.pull_decline import PullDeclined
from llm_preserver.render import clean_text
from llm_preserver.revision import fetch_repo_info, revision_line


def run_pull(
    path: Path,
    repo_id: str,
    client: HubClientProtocol,
    *,
    include: list[str],
    select_all: bool = False,
    roles: tuple[str, ...] = (),
    base_model: str | None = None,
    refresh_docs: bool = False,
    plan: bool = False,
    yes: bool = False,
    repo_info: RepoInfo | None = None,
    resume_hint: bool = False,
    hf_logging: bool = False,
    revision: str | None = None,
) -> None:
    """Run the pull flow: interactive listing, plan or pull, exit mapping.

    Args:
        path: An initialized archive root (caller has verified it).
        repo_id: Exact hub repo id to pull from.
        client: The hub seam (real client or a test double).
        include: fnmatch patterns; empty (without ``select_all``)
            triggers the interactive file listing.
        select_all: Whole-tree snapshot mode (``--whole-repo``).
        roles: Roles to assign at pull time.
        base_model: Curator-asserted lineage for the record.
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
        revision: The ``--revision`` branch, tag, or commit (spec 0024);
            None reads the default branch exactly as before. Never set
            together with ``repo_info`` — discover has no revision.

    Raises:
        typer.Exit: The fault-domain exit for any pull failure; exit
            130 when Ctrl-C interrupts the transfer; or **exit 0** when
            the human declines at a prompt (spec 0021), which prints
            one plain line and is the only non-failure exit raised
            from inside the try.
    """
    # Spec 0007: a hint is owed whenever the pull's shape was assembled
    # interactively; hint holds the printed line for the Ctrl-C repeat.
    emit_hint = resume_hint or (not select_all and not include)
    hint: str | None = None
    # Spec 0014: a pull that moved no bytes and wrote no record must
    # not end with the pull-success line — scripts and scrollback key
    # on it.
    no_op = False
    metadata_only = False
    # Bound before the try, not inside it. This is not a bug fix — as
    # the first statement in the body it was always bound by the time
    # anything could raise — but the doc-refresh recovery line (spec
    # 0020) reads it from the handler, and a name the handler depends on
    # should not sit in the block it is recovering from, one inserted
    # line away from being unbound.
    patterns = list(include)
    # Spec 0024: the commit a --revision resolved to, read by the
    # doc-refresh handler — bound before the try for the same reason as
    # patterns. None whenever no --revision was given, which keeps every
    # composed command byte-identical to the pre-0024 shape.
    resolved_revision: str | None = None
    try:
        info = repo_info
        if revision is not None:
            # Resolve once, before selection or transfer: the inventory,
            # every download, and every composed command use this commit.
            # Fetched here (one call, handed down) so the hints know it.
            info = fetch_repo_info(client, repo_id, revision)
            resolved_revision = info.commit
            if not plan:
                # The plan report carries its own copy of this line.
                typer.echo(revision_line(revision, info.commit))
        if not select_all and not patterns:
            if info is None:
                info = fetch_repo_info(client, repo_id, None)
            status = picker_status(path, repo_id, info)
            patterns = prompt_for_selection(info, repo_id, status)
        if plan:
            run_plan(
                path,
                repo_id,
                client,
                info,
                patterns=patterns,
                select_all=select_all,
                roles=roles,
                base_model=base_model,
                refresh_docs=refresh_docs,
                emit_hint=emit_hint,
                hf_logging=hf_logging,
                requested_revision=revision,
            )
            return

        def capture_resume_hint(resolved_model: str) -> None:
            # Runs after every confirmation, before the first byte
            # (pull_model's on_transfer_start seam). resolved_model is
            # the selection the human just confirmed — replaying it as
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
                roles=roles,
                base_model=base_model,
                refresh_docs=refresh_docs,
                hf_logging=hf_logging,
                revision=resolved_revision,
            )
            if hint is not None and emit_hint:
                typer.echo(hint)

        def mark_no_op() -> None:
            nonlocal no_op
            no_op = True

        def mark_metadata_only() -> None:
            nonlocal metadata_only
            metadata_only = True

        model_dir = pull_model(
            path,
            repo_id,
            client,
            include=patterns,
            roles=roles,
            base_model=base_model,
            repo_info=info,
            refresh_docs=refresh_docs,
            select_all=select_all,
            # confirm_or_stop sanitizes hub-supplied prompt text and
            # converts unanswerable prompts to deterministic exits.
            confirm=lambda prompt: confirm_or_stop(prompt, yes),
            on_transfer_start=capture_resume_hint,
            on_no_op=mark_no_op,
            on_metadata_only=mark_metadata_only,
            pin_archived_commit=revision is not None,
        )
    except PullDeclined as exc:
        # Spec 0021: the human answered the question — typed q, said no,
        # or pressed Ctrl-D at an interactive prompt. Nothing was
        # pulled, but nothing failed either, so this is a plain line on
        # stdout at exit 0, never the `error [domain]:` shape. Deliberately
        # NOT a PullError: exit_for_pull_error cannot reach it, so no
        # future fault domain can reclassify a decline as a failure.
        typer.echo(clean_text(str(exc), single_line=True))
        raise typer.Exit(code=0) from None
    except KeyboardInterrupt:
        # Ctrl-C mid-transfer (spec 0007): repeat the hint as the final
        # line — directly above the next shell prompt — then exit
        # 128 + SIGINT. Never swallowed into a retry.
        if hint is not None:
            typer.echo(hint)
        raise typer.Exit(code=130) from None
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    except UnmigratedArchiveError as exc:
        # pull refuses before calling here; discover reaches this gate
        # only after the picker, so it must map to the same exit 2.
        typer.echo(f"error [user input]: {clean_text(str(exc), single_line=True)}", err=True)
        raise typer.Exit(code=2) from exc
    except PullError as exc:
        exit_exc = exit_for_pull_error(exc)
        if isinstance(exc, PullDocRefreshError):
            # Spec 0020: this stop fires inside prepare_pull — before
            # the on_transfer_start seam that composes the 0007 hint —
            # so the human is told to re-run with a flag and has no
            # command to append it to. Compose it here, from the shape
            # this run already holds. Dispatch is on the type, never on
            # the message text.
            recovery = compose_doc_refresh_hint(
                repo_id,
                path,
                include=patterns,
                select_all=select_all,
                roles=roles,
                base_model=base_model,
                hf_logging=hf_logging,
                revision=resolved_revision,
            )
            if recovery is not None:
                typer.echo(recovery, err=True)
        if isinstance(exc, PullInvalidIdError):
            # Spec 0013: an Ollama-shaped id gets the deterministic
            # recovery command appended to the 0011 error. Detection
            # lives in the error path only; the id is never rewritten
            # and the exit code is unchanged.
            shape_hint = ollama_shape_hint(repo_id)
            if shape_hint is not None:
                typer.echo(shape_hint, err=True)
        raise exit_exc from exc
    if metadata_only:
        final = f"{repo_id} was already archived; recorded the metadata you passed, no files pulled"
    elif no_op:
        final = f"{repo_id} is already archived in {model_dir}; nothing new to pull"
    else:
        final = f"pulled {repo_id} into {model_dir}"
    typer.echo(clean_text(final, single_line=True))
