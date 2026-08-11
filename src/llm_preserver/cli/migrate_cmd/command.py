"""The migrate command: convert an archive to one directory per repo.

The escape hatch for ADR 0003. Preview-then-confirm like ``remove``
(spec 0010), because a bulk relocation of hundreds of gigabytes is not
something to discover after the fact — and, like remove, a
non-interactive run without ``--yes`` refuses rather than act on a
piped answer.

Nothing here re-downloads or re-hashes: paths *inside* an artifact are
unchanged, so recorded digests stay true and the manifests regenerate
from the record.
"""

import shlex
import sys
from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError, require_archive
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.cli.migrate_cmd.preview import echo_plan
from llm_preserver.cli.migrate_cmd.progress import MigrateProgress
from llm_preserver.migrate import (
    MigrateError,
    MigrateEvents,
    MigrateUserError,
    copy_migration,
    execute_migration,
    plan_migration,
)
from llm_preserver.render import clean_text


def _rerun_command(path: Path, repos: list[str], dest: Path | None) -> str:
    """The exact command to resume with, quoted for paste (spec 0007)."""
    parts = ["llm-preserver", "migrate", str(path.resolve())]
    if dest is not None:
        parts.extend(["--to", str(dest.resolve())])
    for repo in repos:
        parts.extend(["--repo", repo])
    return " ".join(shlex.quote(clean_text(part, single_line=True)) for part in parts)


@app.command()
def migrate(
    path: ArchivePath,
    repo: Annotated[
        list[str] | None,
        typer.Option(
            "--repo",
            help="Convert only this <owner>/<repo> directory; repeatable.",
        ),
    ] = None,
    to: Annotated[
        Path | None,
        typer.Option(
            "--to",
            help="Write a converted copy at this root instead of migrating in place.",
        ),
    ] = None,
    view_dest: Annotated[
        list[str] | None,
        typer.Option(
            "--view-dest",
            help="A runtime-view destination to name in the refresh hint; repeatable. "
            "Never opened — migrate only prints the command to re-run.",
        ),
    ] = None,
    plan_only: Annotated[
        bool,
        typer.Option("--plan", help="Show what would move and change nothing."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the confirmation prompt (scripted use)."),
    ] = False,
) -> None:
    """Convert an archive to one directory per source repo (ADR 0003)."""
    repos = list(repo or [])
    dests = [Path(dest) for dest in (view_dest or [])]
    try:
        require_archive(path)
        plan = plan_migration(path, repos or None)
    except MigrateUserError as exc:
        typer.echo(f"error [user input]: {clean_text(str(exc), single_line=True)}", err=True)
        raise typer.Exit(code=2) from exc
    except (ArchiveError, MigrateError) as exc:
        raise fail(str(exc)) from exc

    echo_plan(plan, path, dests)
    if plan_only or plan.is_empty:
        return

    if not yes:
        if not sys.stdin.isatty():
            typer.echo(
                "error: refusing to migrate without a confirmation; "
                "pass --yes for non-interactive use",
                err=True,
            )
            raise typer.Exit(code=2)
        try:
            confirmed = typer.confirm("Migrate?")
        except typer.Abort:
            typer.echo("nothing migrated")
            return
        if not confirmed:
            typer.echo("nothing migrated")
            return

    progress = MigrateProgress()
    events = MigrateEvents(on_directory_start=progress.on_directory_start, on_file=progress.on_file)
    try:
        if to is not None:
            copy_migration(path, to, repos or None, events)
        else:
            execute_migration(path, plan, events)
    except KeyboardInterrupt:
        # There is no resume state to lose — the next plan re-derives
        # from disk — so the one thing worth printing is how to pick up
        # (spec 0007's contract).
        typer.echo("interrupted — migration incomplete", err=True)
        typer.echo(f"resume with: {_rerun_command(path, repos, to)}")
        raise typer.Exit(code=130) from None
    except MigrateUserError as exc:
        typer.echo(f"error [user input]: {clean_text(str(exc), single_line=True)}", err=True)
        raise typer.Exit(code=2) from exc
    except MigrateError as exc:
        raise fail(str(exc)) from exc

    directories = len(plan.units)
    noun = "directory" if directories == 1 else "directories"
    where = f" into {to}" if to is not None else ""
    typer.echo(f"migrated {directories} {noun}{where}")
