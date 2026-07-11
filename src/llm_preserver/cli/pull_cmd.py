"""The pull command: the CLI's only network-touching surface.

Fault-domain exceptions map to four distinct nonzero exit codes so a
human or an agent can triage a failure without reading source
(spec 0003).
"""

import logging
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError, require_archive
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.hub import (
    HubClientProtocol,
    PullEnvError,
    PullError,
    PullHubError,
    PullIntegrityError,
    PullUserError,
    RepoInfo,
)
from llm_preserver.pull import pull_model
from llm_preserver.render import clean_text

logger = logging.getLogger(__name__)

_PULL_FAULT_DOMAINS: list[tuple[type[PullError], str, int]] = [
    # (class, domain label, exit code) — four distinct nonzero codes for
    # triage without reading source; code 1 stays archive/usage (spec 0003).
    (PullUserError, "user input", 2),
    (PullEnvError, "local environment", 3),
    (PullHubError, "hub-side", 4),
    (PullIntegrityError, "integrity", 5),
]


def _make_hub_client() -> HubClientProtocol:
    """Build the hub client through the package's patchable seam.

    Tests replace ``llm_preserver.cli.HubClient``; resolving the
    attribute at call time (not import time) is what makes that seam
    work across the package split.
    """
    import llm_preserver.cli as cli_package

    client: HubClientProtocol = cli_package.HubClient()
    return client


def _setup_logging(verbose: bool) -> None:
    """Configure stdlib logging: concise by default, DEBUG on --verbose.

    The handler goes on the ``llm_preserver`` package logger only —
    never the root logger. A root logger at DEBUG would also emit
    httpx/huggingface_hub request logging (URLs, auth state), which
    must stay out of this tool's output (public-repo hygiene).
    """
    package_logger = logging.getLogger("llm_preserver")
    package_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    package_logger.propagate = False
    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        package_logger.addHandler(handler)


def _log_underlying_failure(exc: PullError) -> None:
    """Log the client exception behind a pull failure, at DEBUG only.

    Logs the exception type and the hub's own status/server message —
    never the request, headers, or ``str()`` of anything that could
    carry the Authorization header or token (public-repo hygiene).
    """
    cause = exc.__cause__
    if cause is None:
        return
    response = getattr(cause, "response", None)
    # getattr chains return Any by nature; both values render via %s.
    status = getattr(response, "status_code", None)
    server_message = getattr(cause, "server_message", None)
    logger.debug(
        "underlying client failure: %s (HTTP status %s, server message %s)",
        type(cause).__name__,
        status,
        server_message,
    )


def _confirm_or_stop(prompt: str, assume_yes: bool) -> bool:
    """Confirm interactively; deterministic stop when stdin cannot answer.

    ``--yes`` auto-accepts the *size* confirmation only — grouping is an
    identity decision that needs an explicit ``--model`` value, never a
    blanket yes. When the prompt cannot be answered (non-interactive
    stdin, exhausted piped input), click raises ``Abort``; that becomes
    a ``PullUserError`` (exit 2) naming the bypass, so scripted pulls
    never die with an undocumented exit 1 (spec 0004 adjudications).
    Prompt classification keys on the strings ``pull_model`` composes —
    the tool owns both sides of this seam.
    """
    cleaned = clean_text(prompt, single_line=True)
    is_size_confirm = cleaned.startswith("pull ")
    if assume_yes and is_size_confirm:
        return True
    try:
        return bool(typer.confirm(cleaned))
    # typer vendors click, so catch its own Abort, not the click
    # package's (they are different classes).
    except typer.Abort:
        if is_size_confirm:
            hint = "re-run with --yes to accept the size confirmation"
        elif "every weight" in cleaned:
            hint = "narrow --include, or run interactively"
        else:
            hint = "pass --model <creator>/<model> to choose the canonical model directory"
        raise PullUserError(f"confirmation needed but stdin is not interactive: {hint}") from None


def _prompt_for_selection(info: RepoInfo, repo_id: str) -> list[str]:
    """List the repo's files with sizes and prompt for include patterns.

    Takes the already-fetched metadata — one metadata call per pull
    (spec 0003), shared with ``pull_model`` via its ``repo_info`` seam.
    """
    typer.echo(f"files in {repo_id}:")
    for repo_file in info.files:
        size = "?" if repo_file.size is None else str(repo_file.size)
        typer.echo(f"  {size:>15}  {clean_text(repo_file.path, single_line=True)}")
    raw = typer.prompt(
        "files to pull (comma-separated fnmatch patterns)", default="", show_default=False
    )
    return [pattern.strip() for pattern in raw.split(",") if pattern.strip()]


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
            "--all",
            help="Full snapshot: download the repo's whole tree (excludes --include).",
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
    """Pull selected files (or with --all, the whole tree) from a Hugging Face repo."""
    _setup_logging(verbose)
    if select_all and include:
        # Mutually exclusive shapes (spec 0004); refuse before any
        # network call or client construction.
        typer.echo(
            "error [user input]: --all and --include are mutually exclusive; "
            "pass --all for the whole tree or --include patterns for a selection",
            err=True,
        )
        raise typer.Exit(code=2)
    # Fail fast on a bad archive path — before any network call or prompt.
    try:
        require_archive(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    client = _make_hub_client()
    try:
        patterns = list(include or [])
        info: RepoInfo | None = None
        if not select_all and not patterns:
            info = client.repo_info(repo_id)
            patterns = _prompt_for_selection(info, repo_id)
        model_dir = pull_model(
            path,
            repo_id,
            client,
            include=patterns,
            model=model,
            roles=tuple(role or ()),
            repo_info=info,
            refresh_docs=refresh_docs,
            select_all=select_all,
            # _confirm_or_stop sanitizes hub-supplied prompt text and
            # converts unanswerable prompts to deterministic exits.
            confirm=lambda prompt: _confirm_or_stop(prompt, yes),
        )
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    except PullError as exc:
        _log_underlying_failure(exc)
        for domain_class, domain_label, exit_code in _PULL_FAULT_DOMAINS:
            if isinstance(exc, domain_class):
                typer.echo(
                    f"error [{domain_label}]: {clean_text(str(exc), single_line=True)}",
                    err=True,
                )
                raise typer.Exit(code=exit_code) from exc
        raise fail(str(exc)) from exc  # unreachable: domains cover PullError
    typer.echo(f"pulled {repo_id} into {model_dir}")
