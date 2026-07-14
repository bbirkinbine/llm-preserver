"""The verify command (spec 0009): audit disk against records.

Read-only over payloads and records; the one write is the regenerable
``manifest-sha256.txt`` sidecar. Never touches the network. Exit codes
are the cron contract: 0 clean, 1 archive/usage, 2 unknown --model,
5 drift, 130 interrupted.
"""

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError, inventory
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.records import ID_COMPONENT_RE
from llm_preserver.render import clean_text
from llm_preserver.verify import ModelVerifyResult, VerifyReport, verify_archive

_STATE_LABELS = {"no-record": "no record", "record-unreadable": "record unreadable"}


def _echo_result(result: ModelVerifyResult) -> None:
    """Print one model's result line plus its per-file detail lines.

    Called as each model completes — this stream is the progress
    display for long runs as well as the report body.
    """
    label = _STATE_LABELS.get(result.state, result.state)
    typer.echo(clean_text(f"{result.model_id}  {label}", single_line=True))
    for problem in result.problems:
        typer.echo(clean_text(f"  {problem.path}: {problem.detail}", single_line=True))
    for rel_path in result.unhashed:
        typer.echo(clean_text(f"  unhashed (no recorded sha256): {rel_path}", single_line=True))
    for rel_path in result.unrecorded:
        typer.echo(
            clean_text(f"  unrecorded (on disk, not in record): {rel_path}", single_line=True)
        )
    if result.manifest_error is not None:
        # A warning, not drift: the payload verdict above stands; only
        # the sidecar refresh failed (e.g. a read-only-mounted archive).
        typer.echo(
            clean_text(f"  manifest not refreshed: {result.manifest_error}", single_line=True),
            err=True,
        )


def _summary_line(report: VerifyReport) -> str:
    """Archive-wide totals, e.g. ``2 models: 1 valid, 1 incomplete``."""
    counts = Counter(result.state for result in report.models)
    order = ("valid", "complete", "incomplete", "invalid", "no-record", "record-unreadable")
    parts = [
        f"{counts[state]} {_STATE_LABELS.get(state, state)}" for state in order if counts[state]
    ]
    total = len(report.models)
    noun = "model" if total == 1 else "models"
    return f"{total} {noun}: {', '.join(parts)}"


def _reject_unknown_model(path_arg: Path, model: str) -> typer.Exit:
    """Error for a --model that matches no model directory: exit 2.

    Prints the archive's model ids so a typo self-corrects without a
    separate ``status`` round-trip (spec 0009).
    """
    typer.echo(
        clean_text(f"error: no model directory for {model} in {path_arg}", single_line=True),
        err=True,
    )
    model_ids = [summary.model_id for summary in inventory(path_arg)]
    if model_ids:
        typer.echo("archived models:", err=True)
        for model_id in model_ids:
            typer.echo(clean_text(f"  {model_id}", single_line=True), err=True)
    return typer.Exit(code=2)


@app.command()
def verify(
    path: ArchivePath,
    model: Annotated[
        str | None,
        typer.Option(help="Audit only this <creator>/<model> instead of the whole archive."),
    ] = None,
    quick: Annotated[
        bool,
        typer.Option(
            "--quick",
            help="Existence and size checks only — no hashing, no manifest refresh.",
        ),
    ] = False,
) -> None:
    """Audit the archive against its records (complete vs valid)."""
    try:
        if model is not None:
            creator, sep, name = model.partition("/")
            if (
                not sep
                or not ID_COMPONENT_RE.fullmatch(creator)
                or not ID_COMPONENT_RE.fullmatch(name)
            ):
                raise fail(f"model id must look like <creator>/<model>, got {model!r}")
        if quick:
            typer.echo("quick check: hashes were not checked (existence and size only)")
        report = verify_archive(path, model=model, quick=quick, on_result=_echo_result)
    except KeyboardInterrupt:
        typer.echo("interrupted — audit incomplete", err=True)
        raise typer.Exit(code=130) from None
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    if model is not None and not report.models:
        raise _reject_unknown_model(path, model)
    if not report.models:
        typer.echo("archive is empty (no models)")
        return
    typer.echo(_summary_line(report))
    if report.drifted:
        raise typer.Exit(code=5)
