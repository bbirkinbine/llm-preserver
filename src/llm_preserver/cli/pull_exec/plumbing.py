"""CLI session plumbing shared by the pull and discover commands.

The hub-client seam, logging setup, and the fault-domain exit mapping
(spec 0003): four distinct nonzero exit codes so a human or an agent
can triage a failure without reading source.
"""

import logging
import os

import typer
from huggingface_hub.utils import logging as hf_hub_logging

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


def setup_logging(verbose: bool, hf_logging: bool = False) -> None:
    """Configure stdlib logging: concise by default, DEBUG on --verbose.

    The handler goes on the ``llm_preserver`` package logger only —
    never the root logger. A root logger at DEBUG would also emit
    httpx/huggingface_hub request logging (URLs, auth state), which
    must stay out of this tool's output (public-repo hygiene).

    Args:
        verbose: DEBUG on the ``llm_preserver`` package logger.
        hf_logging: Surface the HF client's own transfer telemetry
            (spec 0008): Xet stall/retry events via ``RUST_LOG=info``
            and the ``huggingface_hub`` logger at info. Pinned to info,
            never debug — the client's debug tier logs request URLs
            (auth-adjacent), which must not be one flag away.
    """
    package_logger = logging.getLogger("llm_preserver")
    package_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    package_logger.propagate = False
    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        package_logger.addHandler(handler)
    if hf_logging:
        _enable_hf_telemetry()


def _enable_hf_telemetry() -> None:
    """Raise the HF client's own logging to info (spec 0008).

    The Xet byte-transfer layer (Rust) filters its console output at
    ``warn`` by default and takes ``RUST_LOG`` as the override, read
    once when the runtime initializes at first transfer — so this must
    run at CLI startup, before any hub-client touch. A ``RUST_LOG``
    already present in the inherited environment is a user's own
    filter and wins over the flag.

    Source: https://github.com/huggingface/xet-core (Apache-2.0),
    xet_runtime/src/logging/constants.rs (console default ``warn``,
    ``RUST_LOG`` override); fetched 2026-07-13.
    """
    inherited = os.environ.get("RUST_LOG")
    if inherited is None:
        os.environ["RUST_LOG"] = "info"
        # Healthy transfers produce zero vendor output at info
        # (verified live, spec 0008): announce activation once so
        # silence reads as "healthy", not "flag broken".
        logger.info(
            "--hf-logging active: Xet and huggingface_hub telemetry at info; "
            "healthy transfers are silent — stalls, retries, and rate-limit "
            "waits will show here"
        )
    else:
        # A defeated flag must never read as a broken one: an inherited
        # RUST_LOG (including set-but-empty, a likely accident) keeps
        # the Xet layer on the user's own filter, and this says so
        # (spec 0008 adjudication 2026-07-13).
        logger.info(
            "inherited RUST_LOG=%r wins over --hf-logging's Xet filter "
            "(unset it to see Xet stall/retry telemetry at info); "
            "huggingface_hub telemetry at info",
            clean_text(inherited, single_line=True),
        )
    # The library ships its own stderr handler on this logger; setting
    # verbosity is all that is needed. Info only — see setup_logging.
    hf_hub_logging.set_verbosity(logging.INFO)


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
