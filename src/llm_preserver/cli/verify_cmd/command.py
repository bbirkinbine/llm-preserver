"""The verify command: argument surface over the audit core.

Read-only over payloads and records; the one write is the regenerable
``manifest-sha256.txt`` sidecar. Never touches the network. Exit codes
are the cron contract: 0 clean, 1 archive/usage (including an
``unmigrated`` layout, spec 0017), 2 unknown/ambiguous ``--repo``,
5 drift, 130 interrupted.
"""

import sys
from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError, require_archive
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.cli.model_errors import (
    reject_unknown_model,
    reject_unknown_staging_model,
    split_model_id,
)
from llm_preserver.cli.repo_option import resolve_repo_alias
from llm_preserver.cli.verify_cmd.progress import ProgressRenderer
from llm_preserver.cli.verify_cmd.render import (
    _echo_result,
    _staging_line,
    _summary_line,
)
from llm_preserver.model_scan import staging_leftovers
from llm_preserver.render import clean_text
from llm_preserver.verify import ModelVerifyResult, ProgressEvents, verify_archive


def _run_staging_scan(path: Path, model: str | None) -> None:
    """Report abandoned ``.staging/`` downloads only — no audit, no hashing.

    The ``--staging`` short-circuit: it never walks ``models/``, hashes
    nothing, and writes nothing (not even the manifest sidecar a full
    verify refreshes). A leftover is informational, so a clean scan and
    a scan that finds leftovers both exit 0.
    """
    try:
        require_archive(path)
        leftovers = staging_leftovers(path)
    except (ArchiveError, OSError) as exc:
        # An unreadable .staging/ (foreign-uid copy, NAS fault) is a
        # clean exit-1, never a traceback — same contract as a bad path.
        raise fail(str(exc)) from exc
    if model is not None:
        split_model_id(model)  # malformed shape → exit 1, before any listing
        scoped = [left for left in leftovers if left.model_id == model]
        if not scoped:
            raise reject_unknown_staging_model(path, model, [left.model_id for left in leftovers])
        leftovers = scoped
    if not leftovers:
        typer.echo("no abandoned downloads in .staging/")
        return
    for left in leftovers:
        typer.echo(clean_text(_staging_line(left), single_line=True))


def _echo_leftover_footer(path: Path, model: str | None) -> None:
    """Print the informational abandoned-download note, if any.

    Runs on every plain-verify path (including ``--quick`` and an
    empty-``models/`` archive) so a routine audit never silently hides a
    forgotten download. Never changes the exit code, and never raises: a
    symlinked or unreadable ``.staging/`` is surfaced by ``--staging``
    (exit 1), not by crashing an otherwise-successful audit.
    """
    try:
        leftovers = staging_leftovers(path)
    except (ArchiveError, OSError):
        # Best-effort: a symlinked or unreadable .staging/ is surfaced by
        # --staging (exit 1), never by crashing a successful audit.
        return
    if model is not None:
        leftovers = [left for left in leftovers if left.model_id == model]
    count = len(leftovers)
    if count == 0:
        return
    noun = "download" if count == 1 else "downloads"
    typer.echo(f"note: {count} abandoned {noun} in .staging/ — run 'verify --staging'")


@app.command()
def verify(
    path: ArchivePath,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo",
            help="Audit only this <owner>/<repo> instead of the whole archive.",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", hidden=True),
    ] = None,
    quick: Annotated[
        bool,
        typer.Option(
            "--quick",
            help="Existence and size checks only — no hashing, no manifest refresh.",
        ),
    ] = False,
    staging: Annotated[
        bool,
        typer.Option(
            "--staging",
            help="Report abandoned .staging/ downloads only — no audit, no hashing.",
        ),
    ] = False,
) -> None:
    """Audit the archive against its records (complete vs valid)."""
    model = resolve_repo_alias(repo, model)
    if staging:
        try:
            _run_staging_scan(path, model)
        except KeyboardInterrupt:
            typer.echo("interrupted — scan incomplete", err=True)
            raise typer.Exit(code=130) from None
        return
    renderer = ProgressRenderer(sys.stderr)

    def emit(result: ModelVerifyResult) -> None:
        renderer.finish_line()
        _echo_result(result)

    try:
        if model is not None:
            split_model_id(model)
        if quick:
            typer.echo("quick check: hashes were not checked (existence and size only)")
        report = verify_archive(
            path,
            model=model,
            quick=quick,
            on_result=emit,
            events=ProgressEvents(
                on_model_start=renderer.on_model_start,
                on_file_start=renderer.on_file_start,
                on_file_bytes=renderer.on_file_bytes,
            ),
        )
    except KeyboardInterrupt:
        renderer.finish_line()
        typer.echo("interrupted — audit incomplete", err=True)
        raise typer.Exit(code=130) from None
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    if model is not None and not report.models:
        raise reject_unknown_model(path, model)
    if not report.models:
        typer.echo("archive is empty (no models)")
        _echo_leftover_footer(path, model)
        return
    typer.echo(_summary_line(report))
    # The footer is informational and prints before the drift exit — a
    # forgotten download must surface even when the audit itself fails.
    _echo_leftover_footer(path, model)
    if report.drifted:
        raise typer.Exit(code=5)
    if report.unmigrated:
        # Exit 1, not 5: nothing is damaged, but a scheduled verify must
        # go red until the layout is converted (spec 0017 criterion 3).
        raise typer.Exit(code=1)
