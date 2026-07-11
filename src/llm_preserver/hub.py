"""Hub access seam: the only module that imports ``huggingface_hub``.

Isolating every client call (and the ``httpx`` exception types it
raises) behind this module localizes upstream API churn to one seam
(spec 0003, client-churn posture). Pull orchestration and the CLI see
only ``RepoInfo``/``RepoFile`` data and the four fault-domain
``Pull*Error`` exceptions.

Auth is ambient: ``huggingface_hub`` discovers ``HF_TOKEN`` or the
``hf auth login`` token file on its own. This module passes no token
arguments, stores nothing, and never reads or logs the token value.

API facts (exception hierarchy and status codes, ``model_info`` fields,
``hf_hub_download`` local-dir behavior) verified against the installed
``huggingface_hub`` 1.23.0 and its official docs (Apache-2.0),
retrieved 2026-07-10 — sources pinned in
``docs/specs/0003-selective-pull.md`` → "External references":

- https://huggingface.co/docs/huggingface_hub/package_reference/hf_api
- https://huggingface.co/docs/huggingface_hub/guides/download
- https://huggingface.co/docs/huggingface_hub/package_reference/utilities
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from huggingface_hub import HfApi, hf_hub_download
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


@dataclass(frozen=True)
class RepoFile:
    """One file in a hub repo, from the single metadata call.

    Attributes:
        path: Repo-relative filename, verbatim from the hub.
        size: Size in bytes, or None when the hub does not report one.
        sha256: Hub-declared SHA256 (LFS files only); None means the
            hub publishes no hash for this file.
    """

    path: str
    size: int | None
    sha256: str | None


@dataclass(frozen=True)
class RepoInfo:
    """Repo metadata needed for selection, grouping, and the record.

    Attributes:
        commit: The resolved commit hash the file list was read at —
            the pin every pull records (a branch name is a moving
            pointer, not provenance).
        files: Every file in the repo at that commit.
        base_model: The model card's ``base_model``, used to group a
            quant repo under its canonical model; None when absent.
        pipeline_tag: The repo's ``pipeline_tag``, recorded verbatim.
        license: The model card's license label, or None.
    """

    commit: str
    files: list[RepoFile]
    base_model: str | None
    pipeline_tag: str | None
    license: str | None


class HubClientProtocol(Protocol):
    """Structural seam between pull orchestration and hub access.

    Implementations raise the fault-domain ``Pull*Error`` exceptions;
    the real client maps ``huggingface_hub`` exceptions internally via
    ``map_hub_exception``.
    """

    def repo_info(self, repo_id: str) -> RepoInfo:
        """Fetch repo metadata (files with sizes/hashes, repo facts)."""
        ...

    def download(self, repo_id: str, filename: str, revision: str, dest_dir: Path) -> Path:
        """Download one file at ``revision`` into ``dest_dir``."""
        ...


def _server_message(exc: hf_errors.HfHubHTTPError) -> str:
    """Render the hub's own error text, never the request or headers."""
    return exc.server_message or "no server message"


def map_hub_exception(exc: Exception) -> PullError:
    """Map a hub-client exception to its fault-domain ``Pull*Error``.

    Mapping is by typed exception class and status code, never by
    message string (spec 0003, Notes → Logging). Messages carry the
    upstream error text and a next step, but never the request,
    headers, or token.

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
_MAPPED_EXCEPTIONS = (
    hf_errors.HfHubHTTPError,
    hf_errors.OfflineModeIsEnabled,
    httpx.HTTPError,
    OSError,
)


def _first_str(value: object) -> str | None:
    """Normalize a card-data field that may be a string or list of strings."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


class HubClient:
    """Hugging Face implementation of the hub seam.

    Downloads go through the official client (CDN redirects, retries,
    Xet backend, native within-call resume). Auth is ambient — no
    token arguments anywhere.
    """

    def __init__(self) -> None:
        self._api = HfApi()

    def repo_info(self, repo_id: str) -> RepoInfo:
        """Fetch repo metadata in one ``model_info`` call.

        ``files_metadata=True`` supplies file sizes for selection and
        hub-declared LFS SHA256s for ``verified`` provenance.

        Args:
            repo_id: Exact hub repo id (``namespace/repo``).

        Returns:
            The repo's files and record-relevant facts.

        Raises:
            PullError: The fault-domain mapping of any client failure.
        """
        try:
            info = self._api.model_info(repo_id, files_metadata=True)
        except _MAPPED_EXCEPTIONS as exc:
            raise map_hub_exception(exc) from exc
        if info.sha is None:
            raise PullHubError(f"hub returned no commit hash for {repo_id}: retry later")
        card = info.card_data
        return RepoInfo(
            commit=info.sha,
            files=[
                RepoFile(
                    path=sibling.rfilename,
                    size=sibling.size,
                    sha256=sibling.lfs.sha256 if sibling.lfs is not None else None,
                )
                for sibling in info.siblings or []
            ],
            base_model=_first_str(card.base_model) if card is not None else None,
            pipeline_tag=info.pipeline_tag,
            license=_first_str(card.license) if card is not None else None,
        )

    def download(self, repo_id: str, filename: str, revision: str, dest_dir: Path) -> Path:
        """Download one file at a pinned revision into a staging dir.

        Uses the client's ``local_dir`` mode, which leaves
        ``.cache/huggingface/`` bookkeeping in ``dest_dir``; the caller
        stages, hashes, and moves files, then discards the bookkeeping
        (it must never reach the archive).

        Args:
            repo_id: Exact hub repo id (``namespace/repo``).
            filename: Repo-relative file to fetch.
            revision: The pinned commit hash (never a branch name).
            dest_dir: Staging directory to download into.

        Returns:
            The path of the downloaded file inside ``dest_dir``.

        Raises:
            PullError: The fault-domain mapping of any client failure.
        """
        try:
            local = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                local_dir=dest_dir,
            )
        except _MAPPED_EXCEPTIONS as exc:
            raise map_hub_exception(exc) from exc
        return Path(local)
