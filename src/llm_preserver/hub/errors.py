"""Fault-domain errors and the hub-exception mapping.

Mapping is by typed exception class and status code, never by message
string (spec 0003, Notes → Logging). Messages carry the upstream
error text and a next step, but never the request, headers, or token.

Exception hierarchy and status codes verified against the installed
``huggingface_hub`` 1.23.0 and its official docs (Apache-2.0),
retrieved 2026-07-10 — sources pinned in
``docs/specs/0003-selective-pull.md`` → "External references".
"""

import httpx
from huggingface_hub import errors as hf_errors


class PullError(Exception):
    """Base class for pull failures; subclassed per fault domain."""


class PullUserError(PullError):
    """User-input fault: unknown repo/revision/file, gated repo, bad flags."""


class PullEnvError(PullError):
    """Local-environment fault: network unreachable, disk full, offline mode."""


class PullHubError(PullError):
    """Hub-side fault: 5xx responses, rate limiting, maintenance."""


class PullIntegrityError(PullError):
    """Integrity fault: SHA256 mismatch, or a payload-immutability conflict."""


def _server_message(exc: hf_errors.HfHubHTTPError) -> str:
    """Render the hub's own error text, never the request or headers."""
    return exc.server_message or "no server message"


def map_hub_exception(exc: Exception) -> PullError:
    """Map a hub-client exception to its fault-domain ``Pull*Error``.

    Args:
        exc: The exception raised by ``huggingface_hub`` or ``httpx``.

    Returns:
        The fault-domain exception to raise in its place.
    """
    if isinstance(
        exc,
        (
            # GatedRepoError subclasses RepositoryNotFoundError; 401s
            # also surface as RepositoryNotFoundError by upstream
            # design. RemoteEntryNotFoundError is the file-404 error.
            hf_errors.RepositoryNotFoundError,
            hf_errors.RevisionNotFoundError,
            hf_errors.RemoteEntryNotFoundError,
        ),
    ):
        return PullUserError(
            f"repo, revision, or file not found (or access not granted): {exc}; "
            "check the repo id, and for gated/private repos log in with `hf auth login`"
        )
    if isinstance(exc, hf_errors.HFValidationError):
        # The id never reached the network: the client-side validator
        # rejected its shape (e.g. an Ollama `name:tag` pasted where a
        # hub `<org>/<name>` id is expected — spec 0011). A user-input
        # fault (exit 2), not an environment failure; point at the
        # deterministic recovery path.
        return PullUserError(
            f"not a valid Hugging Face repo id (expected '<org>/<name>'): {exc}; "
            "search the hub for the model by name with `llm-preserver discover <query>`"
        )
    if isinstance(exc, hf_errors.HfHubHTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 429:
            return PullHubError("hub is rate limiting requests (HTTP 429): wait and retry later")
        return PullHubError(
            f"hub-side failure (HTTP {status}): {_server_message(exc)}; retry later"
        )
    if isinstance(exc, hf_errors.OfflineModeIsEnabled):
        return PullEnvError(f"hub client is in offline mode: {exc}; unset HF_HUB_OFFLINE and retry")
    if isinstance(exc, httpx.TransportError):
        return PullEnvError(
            f"network failure talking to the hub: {exc}; check your connection and retry"
        )
    return PullEnvError(f"local environment failure ({type(exc).__name__}): {exc}")


# Every exception family the real client is expected to raise; anything
# outside this tuple is a bug and propagates unmapped. Order-insensitive
# (map_hub_exception checks the most specific classes first).
MAPPED_EXCEPTIONS = (
    hf_errors.HfHubHTTPError,
    hf_errors.OfflineModeIsEnabled,
    # A ValueError subclass the id validator raises before any request;
    # not caught by the HTTP/OS families above, so it must be listed
    # explicitly or it escapes unmapped as a Traceback (spec 0011).
    hf_errors.HFValidationError,
    httpx.HTTPError,
    OSError,
)
