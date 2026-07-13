"""CLI session plumbing shared by the pull and discover commands.

The hub-client seam, logging setup, and the fault-domain exit mapping
(spec 0003): four distinct nonzero exit codes so a human or an agent
can triage a failure without reading source.
"""

import logging

import typer

from llm_preserver.cli.app import fail
from llm_preserver.hub import (
    HubClientProtocol,
    PullEnvError,
    PullError,
    PullHubError,
    PullIntegrityError,
    PullUserError,
)
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
    if server_message is not None:
        # Hub-supplied text headed for a --verbose terminal: same
        # control-character scrub as every other echo of hub data.
        server_message = clean_text(str(server_message), single_line=True)
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
