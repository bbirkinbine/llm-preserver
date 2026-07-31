"""The ``discover`` Typer command: argument surface and mode dispatch.

Normal mode drives the spec 0006 interactive flow (``flow``); match
mode (spec 0013, ``--match-ollama``) runs the read-only digest scan in
``discover_match`` and never touches the archive.
"""

from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError, require_archive
from llm_preserver.cli.app import app, fail
from llm_preserver.cli.discover_cmd.flow import run_discovery
from llm_preserver.cli.discover_match import MATCH_LIMIT_MAX, refuse, run_match
from llm_preserver.cli.pull_exec import (
    exit_for_pull_error,
    make_hub_client,
    setup_logging,
)
from llm_preserver.hub import PullError
from llm_preserver.hub_discovery import PAGE_SIZE


@app.command()
def discover(
    # Both positionals are optional at the Typer level so match mode
    # can run without them; normal mode re-requires both below with
    # exit-2 messages (a mode-dependent signature is unparseable).
    query: Annotated[
        str | None, typer.Argument(help="Free-text hub search (the hub's own results).")
    ] = None,
    # Path comes LAST: Click binds positionals left-to-right, so the
    # env-var fallback only works when the omittable argument trails.
    path: Annotated[
        Path | None,
        typer.Argument(
            envvar="LLM_PRESERVER_ARCHIVE",
            help="Archive root directory (falls back to $LLM_PRESERVER_ARCHIVE).",
        ),
    ] = None,
    match_ollama: Annotated[
        str | None,
        typer.Option(
            "--match-ollama",
            metavar="NAME[:TAG]",
            help="Map a local Ollama model to hub repos by SHA256 digest "
            "(reads the local manifest; prints facts, pulls nothing).",
        ),
    ] = None,
    search: Annotated[
        str | None,
        typer.Option(
            "--search",
            help="Hub search term for --match-ollama (default: the model "
            "name with the tag stripped).",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help="How many hub search results --match-ollama checks "
            f"(default {PAGE_SIZE}, max {MATCH_LIMIT_MAX}; one metadata "
            "call per result).",
        ),
    ] = None,
    plan: Annotated[
        bool,
        typer.Option(
            "--plan",
            help="Dry run the final pull: print what it would do, write nothing.",
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
    """Find a model by name and pull it — search, model tree, pull, no browser."""
    setup_logging(verbose, hf_logging=hf_logging)
    if search is not None and match_ollama is None:
        raise refuse("--search only applies together with --match-ollama")
    if limit is not None and match_ollama is None:
        raise refuse("--limit only applies together with --match-ollama")
    if match_ollama is not None:
        # A stray positional binds to `query` first (left-to-right), so
        # this one check refuses any CLI-given positional; an env-filled
        # `path` is fine — match mode never touches the archive.
        if query is not None:
            raise refuse(
                "--match-ollama takes no positional arguments; "
                "use --search to override the hub search term"
            )
        if plan:
            # Refuse rather than silently ignore (review round
            # 2026-07-31): --plan promises a pull dry run, and match
            # mode never pulls — a swallowed flag reads as a dry run
            # that happened.
            raise refuse("--plan does not apply to --match-ollama; match mode never pulls")
        if limit is not None and not 1 <= limit <= MATCH_LIMIT_MAX:
            raise refuse(f"--limit must be between 1 and {MATCH_LIMIT_MAX}, got {limit}")
        try:
            run_match(make_hub_client(), match_ollama, search, limit=limit or PAGE_SIZE)
        except PullError as exc:
            raise exit_for_pull_error(exc) from exc
        return
    if query is None:
        raise refuse("missing QUERY argument — what should the hub search for?")
    if path is None:
        raise refuse("missing archive PATH argument (or set $LLM_PRESERVER_ARCHIVE)")
    # Fail fast on a bad archive path — before any network call.
    try:
        require_archive(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    client = make_hub_client()
    try:
        run_discovery(path, client, query, plan, hf_logging=hf_logging)
    except PullError as exc:
        raise exit_for_pull_error(exc) from exc
