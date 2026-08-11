"""Rendering the migration preview (spec 0017, criteria 9/17/19).

The preview is the whole safety mechanism for a bulk move: it is what
a human reads before agreeing to relocate hundreds of gigabytes, and
``--yes`` skips the *question*, never the disclosure.
"""

import shlex
from pathlib import Path

import typer

from llm_preserver.migrate import MigratePlan
from llm_preserver.pull_preflight import human_size
from llm_preserver.render import clean_text

STALE_VIEWS_NOTE = (
    "note: any runtime view tree built from this archive points at the old paths and is "
    "stale until refreshed; migrate cannot discover trees it was not told about"
)


def echo_copy_scope(archive_root: Path, dest: Path, repos: list[str]) -> None:
    """Say what ``--to`` will copy, which is not what the plan moves.

    The plan counts only the artifacts that change directory; an
    unscoped ``--to`` copies every model directory. Showing the plan's
    figure in front of a whole-archive copy invites someone to size a
    destination volume from it and fill the disk (review, 2026-08-11).
    """
    from llm_preserver.archive import iter_model_dirs

    selected = [
        (model_id, model_dir)
        for model_id, model_dir in iter_model_dirs(archive_root)
        if not repos or model_id in repos
    ]
    total = sum(
        path.stat().st_size
        for _, model_dir in selected
        for path in model_dir.rglob("*")
        if path.is_file()
    )
    noun = "directory" if len(selected) == 1 else "directories"
    typer.echo(
        f"copying {len(selected)} model {noun} ({human_size(total)}) into {dest} "
        "— the source archive is not modified"
    )


def echo_plan(plan: MigratePlan, archive_root: Path, view_dests: list[Path]) -> None:
    """Print every directory, its target, its bytes, and its removals."""
    if plan.is_empty:
        typer.echo("nothing to migrate: every model directory already holds one repo's files")
        return
    for unit in plan.units:
        typer.echo(clean_text(f"{unit.model_id}  ({unit.kind})", single_line=True))
        for move in unit.moves:
            rel_target = _relative(move.target_dir, archive_root)
            count = len(move.files)
            noun = "file" if count == 1 else "files"
            typer.echo(
                clean_text(
                    f"  {count} {noun} -> {rel_target}  ({human_size(move.total_size)})",
                    single_line=True,
                )
            )
        for directory in unit.removed_dirs:
            # Named before the confirm because it is the one deletion
            # migrate performs (criterion 17) — an os.rmdir that fails
            # rather than recursing, but still a removal.
            typer.echo(
                clean_text(
                    f"  remove empty directory: {_relative(directory, archive_root)}",
                    single_line=True,
                )
            )
    for warning in plan.warnings:
        typer.echo(clean_text(f"  note: {warning}", single_line=True), err=True)
    directories = len(plan.units)
    noun = "directory" if directories == 1 else "directories"
    typer.echo(f"{directories} {noun} to convert, {human_size(plan.total_size)} moving")
    _echo_view_hints(archive_root, view_dests)


def _echo_view_hints(archive_root: Path, view_dests: list[Path]) -> None:
    """Name the ``views`` re-run for each dest, and warn about the rest.

    The path is composed into text and never opened: migrate does not
    touch a view tree, it only says what to run (criterion 19).
    """
    for dest in view_dests:
        # Shell-quoted like every other pasteable command this tool
        # prints (specs 0007/0013): a dest with a space runs against its
        # first word, and one starting with a dash arrives as a flag.
        command = " ".join(
            shlex.quote(clean_text(part, single_line=True))
            for part in ("llm-preserver", "views", str(dest), str(archive_root))
        )
        typer.echo(f"after migrating, refresh this view tree: {command}")
    typer.echo(STALE_VIEWS_NOTE)


def _relative(path: Path, archive_root: Path) -> str:
    """Archive-relative path when possible, absolute otherwise."""
    try:
        return str(path.relative_to(archive_root))
    except ValueError:
        return str(path)
