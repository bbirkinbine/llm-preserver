"""The pull command: argument surface over the shared pull core.

Fault-domain exceptions map to four distinct nonzero exit codes so a
human or an agent can triage a failure without reading source
(spec 0003); the mapping and the execution flow live in ``pull_exec``,
shared with the discover command (spec 0006).
"""

from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError, require_archive
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.cli.pull_exec import make_hub_client, run_pull, setup_logging


@app.command()
def pull(
    repo_id: Annotated[str, typer.Argument(help="Exact hub repo id (<namespace>/<repo>).")],
    # Path comes LAST: Click binds positionals left-to-right, so the
    # env-var fallback only works when the omittable argument trails.
    path: ArchivePath,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="fnmatch pattern selecting files; repeatable."),
    ] = None,
    select_all: Annotated[
        bool,
        typer.Option(
            "--whole-repo",
            help="Full snapshot: download the named repo's whole tree (excludes --include).",
        ),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Canonical model directory (<creator>/<model>) override."),
    ] = None,
    role: Annotated[
        list[str] | None,
        typer.Option("--role", help="Role to assign the model at pull time; repeatable."),
    ] = None,
    refresh_docs: Annotated[
        bool,
        typer.Option(
            "--refresh-docs",
            help="Replace changed upstream documentation files (never weights).",
        ),
    ] = False,
    plan: Annotated[
        bool,
        typer.Option(
            "--plan",
            help="Dry run: print what the pull would do, then exit without downloading or writing.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Auto-accept the size confirmation (never the grouping confirm).",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show per-file progress and client detail.")
    ] = False,
) -> None:
    """Pull selected files (or with --whole-repo, the whole tree) from a Hugging Face repo."""
    setup_logging(verbose)
    if select_all and include:
        # Mutually exclusive shapes (spec 0004); refuse before any
        # network call or client construction.
        typer.echo(
            "error [user input]: --whole-repo and --include are mutually exclusive; "
            "pass --whole-repo for the whole tree or --include patterns for a selection",
            err=True,
        )
        raise typer.Exit(code=2)
    # Fail fast on a bad archive path — before any network call or prompt.
    try:
        require_archive(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    run_pull(
        path,
        repo_id,
        make_hub_client(),
        include=list(include or []),
        select_all=select_all,
        model=model,
        roles=tuple(role or ()),
        refresh_docs=refresh_docs,
        plan=plan,
        yes=yes,
    )
