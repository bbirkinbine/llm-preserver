"""The remove command (spec 0010): the archive's one deletion path.

Whole-model or pattern-scoped, always preview-then-confirm (the preview
is the only safety mechanism — there is no undo). ``--yes`` skips the
question, never the disclosure, so a script's log still records what
was deleted. Exit codes carry the scripting contract (0009 stance):
0 removed / declined, 1 archive/usage, 2 user-input (unknown model,
no-match pattern, unanswerable confirmation), 130 interrupted.
"""

import shlex
import sys
from collections import defaultdict
from pathlib import Path
from typing import Annotated, TextIO

import typer

from llm_preserver.archive import ArchiveError
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.cli.model_errors import reject_unknown_model, split_model_id
from llm_preserver.pull_preflight import human_size
from llm_preserver.remove import (
    ModelNotFound,
    RemoveError,
    RemovePlan,
    RemoveUserError,
    execute_removal,
    plan_removal,
)
from llm_preserver.render import clean_text


class _Progress:
    """Per-file removal lines on stderr, only when it is a terminal.

    The 0009 live-use lesson: silence during a slow delete (an archive
    on NFS) reads as a hang. Gating on ``isatty`` keeps a piped run's
    stdout byte-identical to a progress-free run, so script logs never
    change shape.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._enabled = stream.isatty()

    def on_file(self, rel_path: str) -> None:
        """Announce one deleted file (no-op when stderr is not a TTY)."""
        if not self._enabled:
            return
        self._stream.write(f"removing {clean_text(rel_path, single_line=True)}\n")
        self._stream.flush()


def _echo(line: str) -> None:
    """Print one scrubbed line to stdout."""
    typer.echo(clean_text(line, single_line=True))


def _print_preview(plan: RemovePlan) -> None:
    """Show exactly what a confirmed removal will delete."""
    if plan.whole_model:
        _print_whole_preview(plan)
    else:
        _print_pattern_preview(plan)


def _print_whole_preview(plan: RemovePlan) -> None:
    _echo(plan.model_id)
    if plan.model_dir is None:
        _echo("  no archived model — only interrupted-pull staging leftovers")
        _echo(f"  staging leftovers: {plan.staging_dir}")
        _echo("clears the staging directory")
        return
    grouped: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [count, bytes]
    for planned in plan.files:
        fmt = planned.path.split("/", 1)[0] if "/" in planned.path else planned.path
        grouped[fmt][0] += 1
        grouped[fmt][1] += planned.size or 0
    for fmt in sorted(grouped):
        count, size = grouped[fmt]
        noun = "file" if count == 1 else "files"
        _echo(f"  {fmt}   {count} {noun}   {human_size(size)}")
    if not plan.record_readable:
        _echo("  (record missing or unreadable — file counts read from disk)")
    _echo(f"  staging leftovers: {plan.staging_dir if plan.staging_dir is not None else 'none'}")
    noun = "file" if len(plan.files) == 1 else "files"
    _echo(
        f"deletes {len(plan.files)} {noun} ({human_size(plan.total_size)}) and the model's record"
    )


def _print_pattern_preview(plan: RemovePlan) -> None:
    count = len(plan.files)
    noun = "file" if count == 1 else "files"
    _echo(f"{plan.model_id} — removing {count} {noun} ({human_size(plan.total_size)}):")
    for planned in plan.files:
        flag = "  (unrecorded)" if planned.unrecorded else ""
        _echo(f"  {planned.path}   {human_size(planned.size or 0)}{flag}")
    removed = {planned.path for planned in plan.files if not planned.unrecorded}
    record = plan.record
    if record is None:  # pattern-mode plans always carry a record
        return
    kept = [
        entry.path
        for artifact in record.artifacts
        for entry in artifact.files
        if entry.path not in removed
    ]
    kept_str = ", ".join(kept) if kept else "(no other files)"
    _echo(f"kept: {kept_str}, the record")


def _result_line(plan: RemovePlan) -> str:
    """The audit-trail line printed after a completed removal."""
    noun = "file" if len(plan.files) == 1 else "files"
    if not plan.whole_model:
        return (
            f"removed {len(plan.files)} {noun} ({human_size(plan.total_size)}) "
            f"from {plan.model_id}; record updated"
        )
    if plan.model_dir is None:
        return f"removed staging leftovers for {plan.model_id}"
    return f"removed {plan.model_id} ({len(plan.files)} {noun}, {human_size(plan.total_size)})"


def _rerun_command(model_id: str, archive_path: Path, include: list[str]) -> str:
    """The paste-ready command that re-runs an interrupted removal.

    Absolute archive path (works from any directory), patterns scrubbed
    then shell-quoted so a glob cannot expand on paste. ``--yes`` never
    rides: the re-run earns its own preview and confirmation (0007).
    """
    parts = ["llm-preserver", "remove", model_id, str(archive_path.resolve())]
    for pattern in include:
        parts.extend(["--include", pattern])
    return " ".join(shlex.quote(clean_text(part, single_line=True)) for part in parts)


@app.command()
def remove(
    model_id: Annotated[str, typer.Argument(help="The <creator>/<model> to remove.")],
    path: ArchivePath,
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            help="Remove only files matching this fnmatch pattern (repeatable). "
            "Matches the archived paths shown by 'show', not hub filenames.",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the confirmation prompt (scripted use)."),
    ] = False,
) -> None:
    """Delete a model, or a pattern-scoped subset of its files, from the archive."""
    patterns = list(include or [])
    split_model_id(model_id)  # exit 1 on a malformed id, before any path use
    try:
        plan = plan_removal(path, model_id, patterns or None)
    except ModelNotFound:
        raise reject_unknown_model(path, model_id) from None
    except RemoveUserError as exc:
        typer.echo(f"error: {clean_text(str(exc), single_line=True)}", err=True)
        raise typer.Exit(code=2) from exc
    except RemoveError as exc:
        raise fail(str(exc)) from exc
    except ArchiveError as exc:
        raise fail(str(exc)) from exc

    _print_preview(plan)

    if not yes:
        # This delete is irreversible, so a non-interactive run must opt
        # in explicitly (spec 0010): with no TTY and no --yes there is no
        # trustworthy answer — a piped or inherited 'y' must not stand in
        # for a human. Refuse up front naming the bypass, rather than act
        # on it or hang.
        if not sys.stdin.isatty():
            typer.echo(
                "error: refusing to remove without a confirmation; "
                "pass --yes for non-interactive use",
                err=True,
            )
            raise typer.Exit(code=2)
        try:
            confirmed = typer.confirm("Remove?")
        except typer.Abort:
            # Ctrl-C at an interactive prompt is a decline, not a fault:
            # nothing removed, exit 0 — the same as a typed 'n'.
            typer.echo("nothing removed")
            return
        if not confirmed:
            typer.echo("nothing removed")
            return

    progress = _Progress(sys.stderr)
    try:
        execute_removal(path, plan, on_file=progress.on_file)
    except KeyboardInterrupt:
        typer.echo(_rerun_command(model_id, path, patterns))
        raise typer.Exit(code=130) from None
    except RemoveError as exc:
        # A symlinked rewrite target refused mid-execute, or similar.
        raise fail(str(exc)) from exc
    except OSError as exc:
        # A read-only mount or a permission fault: fail with a specific
        # message, not a bare traceback (verify was hardened the same way
        # in spec 0009). Pattern mode writes the record before any unlink,
        # so a write fault here leaves the record intact, nothing deleted.
        raise fail(f"could not complete removal: {exc}") from exc
    typer.echo(_result_line(plan))
