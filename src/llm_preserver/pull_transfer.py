"""The byte-moving phase of a pull: stage, hash, verify, move, lock.

Split out of ``pull`` so orchestration and transfer stay under the
file-size cap independently. The invariants live here: files are
hashed in staging, verified against the hub-declared hash when one
exists, and only then move into the canonical model directory; a
hash mismatch discards the staged bytes so a retry cannot reuse
them; archived payload is locked read-only after the move.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from llm_preserver.hub import (
    HubClientProtocol,
    PullIntegrityError,
    PullUserError,
    RepoInfo,
)
from llm_preserver.pull_plan import PlannedDownload, sha256_of
from llm_preserver.records import FileEntry

logger = logging.getLogger(__name__)


def _discard_corrupt_staged_file(staging_dir: Path, local: Path, hub_path: str) -> None:
    """Drop a hash-mismatched download so a retry cannot reuse it.

    The client's ``.cache/huggingface`` bookkeeping marks the staged
    file complete; left in place, every retry would reuse the corrupt
    bytes and re-fail forever.
    """
    local.unlink(missing_ok=True)
    metadata = staging_dir / ".cache" / "huggingface" / "download" / f"{hub_path}.metadata"
    metadata.unlink(missing_ok=True)


def _move_into_archive(
    staged: Sequence[tuple[PlannedDownload, Path, str]], model_dir: Path, commit: str
) -> list[FileEntry]:
    """Move fully hashed staged files into place; lock and describe them.

    Consumes the exact target paths planning validated, and re-checks
    containment right before the write — the archive must be impossible
    to escape even if an earlier check regresses. A ``--refresh-docs``
    replacement (``replace_existing``) unlocks the superseded doc file
    first; weight paths never plan a replacement.
    """
    entries: list[FileEntry] = []
    model_root = model_dir.resolve()
    for planned, local, digest in staged:
        target = model_dir / planned.target_rel
        if not target.resolve().is_relative_to(model_root):
            raise PullUserError(
                f"repo supplies an unsafe filename {planned.repo_file.path!r}: "
                f"target escapes the model directory {model_dir}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if planned.replace_existing and target.exists():
            # --refresh-docs: unlock the superseded doc before replacing.
            target.chmod(target.stat().st_mode | 0o200)
        size = local.stat().st_size
        local.replace(target)
        # ADR 0001 payload locking: clear write permission after the move.
        target.chmod(target.stat().st_mode & ~0o222)
        entries.append(
            FileEntry(
                path=planned.target_rel,
                sha256=digest,
                size=size,
                source="original",
                # digest == hub hash was enforced before the move.
                provenance="verified" if planned.repo_file.sha256 is not None else "hashed-locally",
                revision=commit,
            )
        )
    return entries


def download_and_archive(
    client: HubClientProtocol,
    repo_id: str,
    info: RepoInfo,
    to_download: Sequence[PlannedDownload],
    staging_dir: Path,
    model_dir: Path,
) -> list[FileEntry]:
    """Stage, hash, verify, and move every planned download."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[PlannedDownload, Path, str]] = []
    for index, planned in enumerate(to_download, start=1):
        hub_path = planned.repo_file.path
        # "n of m" on every pull (spec 0004, ratified 2026-07-11): a
        # non-TTY run still shows which shard is in flight.
        logger.info("downloading %d of %d: %s", index, len(to_download), hub_path)
        logger.debug("revision %s, staging %s", info.commit, staging_dir)
        local = client.download(
            repo_id=repo_id, filename=hub_path, revision=info.commit, dest_dir=staging_dir
        )
        digest = sha256_of(local)
        declared = planned.repo_file.sha256
        if declared is not None and digest != declared.lower():
            _discard_corrupt_staged_file(staging_dir, local, hub_path)
            raise PullIntegrityError(
                f"sha256 mismatch for {hub_path} after download: hub declared {declared}, "
                f"local hash is {digest}; the file never entered the archive — retry the pull"
            )
        staged.append((planned, local, digest))
    return _move_into_archive(staged, model_dir, info.commit)
