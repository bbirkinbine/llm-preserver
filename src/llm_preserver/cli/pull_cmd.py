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
from llm_preserver.layout import UnmigratedArchiveError, require_migrated_archive
from llm_preserver.render import clean_text


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
        typer.Option("--model", hidden=True),
    ] = None,
    role: Annotated[
        list[str] | None,
        typer.Option("--role", help="Role to assign the model at pull time; repeatable."),
    ] = None,
    base_model: Annotated[
        str | None,
        typer.Option(
            "--base-model",
            help="Record <owner>/<repo> as this model's lineage. Affects the record only, "
            "never where the files land.",
        ),
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
    hf_logging: Annotated[
        bool,
        typer.Option(
            "--hf-logging",
            help="Show the HF client's own transfer telemetry (stalls, retries, backoff).",
        ),
    ] = False,
) -> None:
    """Pull selected files (or with --whole-repo, the whole tree) from a Hugging Face repo."""
    setup_logging(verbose, hf_logging=hf_logging)
    if model is not None:
        # ADR 0003: the destination is a pure function of the typed repo
        # id, so there is no directory left to choose. Kept as a hidden
        # option rather than deleted outright — click's bare "no such
        # option" would not say what to do instead.
        typer.echo(
            "error [user input]: --model is gone; a pull now lands in the directory named by "
            "the repo id you type. Pull the repo id you want archived "
            f"(for {model}, run: pull {model})",
            err=True,
        )
        raise typer.Exit(code=2)
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
    # The content gate rides here too: an unconverted archive must not
    # take new content (spec 0017 criteria 23-25), and refusing before
    # the hub client is even built keeps it off the network.
    try:
        require_archive(path)
        require_migrated_archive(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    except UnmigratedArchiveError as exc:
        typer.echo(f"error [user input]: {clean_text(str(exc), single_line=True)}", err=True)
        raise typer.Exit(code=2) from exc
    run_pull(
        path,
        repo_id,
        make_hub_client(),
        include=list(include or []),
        select_all=select_all,
        roles=tuple(role or ()),
        base_model=base_model,
        refresh_docs=refresh_docs,
        plan=plan,
        yes=yes,
        hf_logging=hf_logging,
    )
