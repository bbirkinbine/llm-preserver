"""Shared pull-execution core for the pull and discover commands.

Extracted from ``pull_cmd`` (spec 0006): the ``discover`` handoff must
land in exactly the pull flow the ``pull`` command runs — same
interactive listing, confirmations, dry-run branch, and fault-domain
exit mapping — so that flow lives once, parameterized with an
optional pre-fetched ``RepoInfo`` (one metadata call per pull, spec
0003).
"""

import fnmatch
import logging
from pathlib import Path, PurePosixPath

import typer

from llm_preserver.archive import ArchiveError
from llm_preserver.cli.app import fail
from llm_preserver.hub import (
    HubClientProtocol,
    PullEnvError,
    PullError,
    PullHubError,
    PullIntegrityError,
    PullUserError,
    RepoInfo,
)
from llm_preserver.pull import pull_model, validated_roles
from llm_preserver.pull_advisory import COMPANION_RULES
from llm_preserver.pull_preflight import require_disk_budget
from llm_preserver.pull_prepare import prepare_pull
from llm_preserver.pull_report import render_plan
from llm_preserver.render import clean_text

logger = logging.getLogger(__name__)

PULL_FAULT_DOMAINS: list[tuple[type[PullError], str, int]] = [
    # (class, domain label, exit code) — four distinct nonzero codes for
    # triage without reading source; code 1 stays archive/usage (spec 0003).
    (PullUserError, "user input", 2),
    (PullEnvError, "local environment", 3),
    (PullHubError, "hub-side", 4),
    (PullIntegrityError, "integrity", 5),
]


def make_hub_client() -> HubClientProtocol:
    """Build the hub client through the package's patchable seam.

    Tests replace ``llm_preserver.cli.HubClient``; resolving the
    attribute at call time (not import time) is what makes that seam
    work across the package split.
    """
    import llm_preserver.cli as cli_package

    client: HubClientProtocol = cli_package.HubClient()
    return client


def setup_logging(verbose: bool) -> None:
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


def log_underlying_failure(exc: PullError) -> None:
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


def exit_for_pull_error(exc: PullError) -> typer.Exit:
    """Map a pull failure to its fault-domain exit, echoing the error."""
    log_underlying_failure(exc)
    for domain_class, domain_label, exit_code in PULL_FAULT_DOMAINS:
        if isinstance(exc, domain_class):
            typer.echo(
                f"error [{domain_label}]: {clean_text(str(exc), single_line=True)}",
                err=True,
            )
            return typer.Exit(code=exit_code)
    return fail(str(exc))  # unreachable: domains cover PullError


def confirm_or_stop(prompt: str, assume_yes: bool) -> bool:
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


def _kind_note(path: str) -> str:
    """Annotate a recognized companion kind (the advisory rules table).

    The same curated data the advisories use, shown where the human
    is actually reading filenames (live-use ask, 2026-07-13: "what is
    imatrix again?").
    """
    name = PurePosixPath(path).name
    for pattern, kind in COMPANION_RULES:
        if fnmatch.fnmatchcase(name, pattern):
            return f"  — {kind}"
    return ""


def prompt_for_selection(info: RepoInfo, repo_id: str) -> list[str]:
    """List the repo's files with sizes and prompt for include patterns.

    Takes the already-fetched metadata — one metadata call per pull
    (spec 0003), shared with ``pull_model`` via its ``repo_info`` seam.
    """
    # repo_id can arrive from hub metadata via discover — same trust
    # class as the file paths below.
    typer.echo(f"files in {clean_text(repo_id, single_line=True)}:")
    for repo_file in info.files:
        size = "?" if repo_file.size is None else str(repo_file.size)
        line = f"  {size:>15}  {repo_file.path}{_kind_note(repo_file.path)}"
        typer.echo(clean_text(line, single_line=True))
    raw = typer.prompt(
        # The leading * matters: patterns match the full repo path, so
        # bare "Q4_K_M*" matches nothing (live mispull, 2026-07-12).
        "files to pull (comma-separated patterns, e.g. *Q4_K_M* or *.gguf,*mmproj*)",
        default="",
        show_default=False,
    )
    return [pattern.strip() for pattern in raw.split(",") if pattern.strip()]


def run_pull(
    path: Path,
    repo_id: str,
    client: HubClientProtocol,
    *,
    include: list[str],
    select_all: bool = False,
    model: str | None = None,
    roles: tuple[str, ...] = (),
    refresh_docs: bool = False,
    plan: bool = False,
    yes: bool = False,
    repo_info: RepoInfo | None = None,
) -> None:
    """Run the pull flow: interactive listing, plan or pull, exit mapping.

    Args:
        path: An initialized archive root (caller has verified it).
        repo_id: Exact hub repo id to pull from.
        client: The hub seam (real client or a test double).
        include: fnmatch patterns; empty (without ``select_all``)
            triggers the interactive file listing.
        select_all: Whole-tree snapshot mode (``--whole-repo``).
        model: Canonical model directory override.
        roles: Roles to assign at pull time.
        refresh_docs: Replace changed upstream doc files.
        plan: Dry run (spec 0005) — report and exit, write nothing.
        yes: Auto-accept the size confirmation.
        repo_info: Pre-fetched metadata (the discover handoff) — spec
            0003's one metadata call per pull; None fetches here.

    Raises:
        typer.Exit: The fault-domain exit for any pull failure.
    """
    try:
        patterns = list(include)
        info = repo_info
        if not select_all and not patterns:
            if info is None:
                info = client.repo_info(repo_id)
            patterns = prompt_for_selection(info, repo_id)
        if plan:
            # Dry run (spec 0005): prepare through the same code path a
            # real pull executes, report, and exit — confirmations are
            # recorded as would-ask lines, never asked. The preflight
            # check runs after the report so an over-budget plan still
            # prints before refusing (exit 3, scripts gate on it).
            # Roles validate here too: plan exit 0 must mean the real
            # command would proceed, and a bad --role would exit 2.
            validated_roles(roles)
            would_ask: list[str] = []

            def record_prompt(prompt: str) -> bool:
                would_ask.append(clean_text(prompt, single_line=True))
                return True

            prep = prepare_pull(
                path,
                repo_id,
                client,
                include=patterns,
                model=model,
                repo_info=info,
                refresh_docs=refresh_docs,
                select_all=select_all,
                confirm=record_prompt,
            )
            for line in render_plan(prep, would_ask):
                if line.startswith("warning:"):
                    # Likely human error (e.g. grouping mismatch) —
                    # highlight it; click strips color off-terminal.
                    typer.secho(line, fg=typer.colors.YELLOW, bold=True)
                else:
                    typer.echo(line)
            require_disk_budget(path, prep.needed_bytes, prep.disk_free)
            return
        model_dir = pull_model(
            path,
            repo_id,
            client,
            include=patterns,
            model=model,
            roles=roles,
            repo_info=info,
            refresh_docs=refresh_docs,
            select_all=select_all,
            # confirm_or_stop sanitizes hub-supplied prompt text and
            # converts unanswerable prompts to deterministic exits.
            confirm=lambda prompt: confirm_or_stop(prompt, yes),
        )
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    except PullError as exc:
        raise exit_for_pull_error(exc) from exc
    typer.echo(clean_text(f"pulled {repo_id} into {model_dir}", single_line=True))
