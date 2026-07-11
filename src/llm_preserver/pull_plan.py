"""Pull planning: grouping, existing-record load, idempotent skip matrix.

Split out of ``pull.py`` (300-line rule): this module decides *where* a
pull lands and *what* it must download; ``pull.py`` stages, verifies,
moves, and records. Every planned download carries its validated
target path — the move phase consumes exactly what planning checked,
so no later join can re-derive (and thereby weaken) the path.

Doc files (README / model card / LICENSE / use-policy) target
``<format>/docs/<namespace>--<repo>/<filename>`` so two source repos
can never collide on ``README.md``; weights target
``<format>/<filename>`` and are absolutely immutable. ``--refresh-docs``
is the explicit choice for replacing changed upstream docs — it never
applies to weight paths (spec 0003, review adjudications 2026-07-10).
"""

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import ValidationError

from llm_preserver.hub import (
    PullEnvError,
    PullIntegrityError,
    PullUserError,
    RepoFile,
    RepoInfo,
)
from llm_preserver.records import (
    ID_COMPONENT_RE,
    RECORD_FILENAME,
    FileEntry,
    ModelRecord,
    load_record,
)
from llm_preserver.selection import checked_target_path, is_doc_file

logger = logging.getLogger(__name__)

_HASH_CHUNK_BYTES = 1 << 20

ConfirmCallback = Callable[[str], bool]
"""Callback that shows the user a prompt and returns their yes/no."""


@dataclass(frozen=True)
class PlannedDownload:
    """One file the pull must fetch, with its validated destination.

    Attributes:
        repo_file: The hub file (path/size/hash) to download.
        target_rel: The model-dir-relative destination, already
            path-safety checked.
        replace_existing: True only for a ``--refresh-docs``
            replacement of a changed doc — the move phase unlocks and
            overwrites the superseded file. Never set for weights.
    """

    repo_file: RepoFile
    target_rel: str
    replace_existing: bool = False


@dataclass(frozen=True)
class PullPlan:
    """What a pull must do: downloads to perform, on-disk files adopted.

    Attributes:
        to_download: Files to fetch, in selection order.
        adopted: Entries for unrecorded on-disk files whose hash
            matched the hub (reconcile-by-hash) — recorded, never
            re-downloaded.
    """

    to_download: list[PlannedDownload]
    adopted: list[FileEntry]


def sha256_of(path: Path) -> str:
    """Stream-hash a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_model_id(
    model: str | None, info: RepoInfo, repo_id: str, confirm: ConfirmCallback
) -> tuple[str, str]:
    """Resolve the canonical ``<creator>/<model>`` directory for a pull.

    A ``--model`` override is used verbatim; otherwise the quant repo's
    ``base_model`` metadata names the grouping, confirmed with the user
    (ADR 0001: a judgment call at download time). No metadata and no
    override is a hard stop, not a guess.

    Raises:
        PullUserError: On no metadata + no override, declined grouping,
            or a malformed model id.
    """
    if model is None:
        if info.base_model is None:
            raise PullUserError(
                f"{repo_id} declares no base_model and no --model override was given; "
                "pass --model <creator>/<model> to choose the canonical model directory"
            )
        if not confirm(f"group {repo_id} under canonical model {info.base_model}?"):
            raise PullUserError(
                f"grouping under {info.base_model} declined: "
                "re-run with --model <creator>/<model> to choose explicitly"
            )
        model = info.base_model
    creator, sep, name = model.partition("/")
    if not sep or not ID_COMPONENT_RE.fullmatch(creator) or not ID_COMPONENT_RE.fullmatch(name):
        raise PullUserError(f"model id must look like <creator>/<model>, got {model!r}")
    return creator, name


def load_existing_record(model_dir: Path) -> ModelRecord | None:
    """Load the model's record if one exists; unreadable is a hard stop.

    Raises:
        PullUserError: If a record file exists but cannot be read —
            pulling on top of an unreadable record risks clobbering it.
    """
    if not (model_dir / RECORD_FILENAME).is_file():
        return None
    try:
        return load_record(model_dir)
    except (ValidationError, ValueError, OSError) as exc:
        raise PullUserError(
            f"existing record in {model_dir} cannot be read ({exc}); "
            "fix or move it before pulling into this model"
        ) from exc


def _immutability_stop(target_rel: str, detail: str, hub_path: str) -> PullIntegrityError:
    """Build the payload-immutability hard stop, naming the way out for docs."""
    way_out = (
        "re-run with --refresh-docs to replace this documentation file"
        if is_doc_file(hub_path)
        else "replacing or adding the new content requires an explicit choice"
    )
    return PullIntegrityError(
        f"{target_rel} {detail}; the archive is payload-immutable — {way_out}"
    )


def _reconcile_unrecorded_file(
    planned: PlannedDownload, target_abs: Path, commit: str
) -> FileEntry:
    """Adopt an on-disk-but-unrecorded file whose hash matches the hub.

    A crash between move and record-write leaves exactly this state;
    hashing proves the bytes are the hub's, so the file is recorded
    (verified, pinned to this pull's commit) instead of refused.

    Raises:
        PullIntegrityError: If the hub publishes no hash or the on-disk
            hash differs — refuse rather than overwrite.
        PullEnvError: If the on-disk file cannot be read.
    """
    declared = planned.repo_file.sha256
    try:
        on_disk_hash = sha256_of(target_abs) if declared is not None else None
    except OSError as exc:
        raise PullEnvError(
            f"cannot read {target_abs} to reconcile it against the hub: {exc}"
        ) from exc
    if declared is None or on_disk_hash != declared.lower():
        raise PullIntegrityError(
            f"{planned.target_rel} exists on disk but is not in the record and cannot be "
            f"reconciled against the hub (hub-declared hash: {declared or 'none published'}); "
            "the archive is payload-immutable — refusing to overwrite an unrecorded file"
        )
    logger.info("adopted unrecorded on-disk file %s (hash matches the hub)", planned.target_rel)
    return FileEntry(
        path=planned.target_rel,
        sha256=declared.lower(),
        size=target_abs.stat().st_size,
        source="original",
        provenance="verified",
        revision=commit,
    )


def plan_downloads(
    selected: Sequence[RepoFile],
    subdir: str,
    model_dir: Path,
    record: ModelRecord | None,
    *,
    repo_id: str,
    commit: str,
    refresh_docs: bool = False,
) -> PullPlan:
    """Classify each selected file: skip, download, adopt, or hard stop.

    The idempotency matrix (spec 0003): a hub-declared hash matching a
    recorded artifact skips ("already archived"); hash-less files fall
    back to a name + size match; changed content is a hard stop —
    unless the file is a doc and ``refresh_docs`` is set, in which case
    it is re-downloaded as a replacement (weights never refresh); a
    recorded file missing on disk is archive drift — warn and
    re-download; an unrecorded on-disk file is adopted when its hash
    matches the hub, refused otherwise. Skips require the record match
    *and* the file present on disk.

    Args:
        selected: The files the selection picked.
        subdir: The format subdirectory the pull lands in.
        model_dir: The target model directory.
        record: The model's existing record, if any.
        repo_id: The source repo — names the per-source doc directory.
        commit: The pull's resolved commit, pinned on adopted files.
        refresh_docs: Allow replacing changed *doc* files, never weights.

    Returns:
        The plan: downloads to perform and on-disk files adopted.

    Raises:
        PullIntegrityError: On any refused content conflict — the
            archive is payload-immutable, never silently overwritten.
        PullUserError: If the repo supplies an unsafe filename.
        PullEnvError: If an on-disk file cannot be read for adoption.
    """
    recorded: dict[str, FileEntry] = {}
    if record is not None:
        for artifact in record.artifacts:
            for file_entry in artifact.files:
                recorded[file_entry.path] = file_entry
    to_download: list[PlannedDownload] = []
    adopted: list[FileEntry] = []
    for repo_file in selected:
        target_rel = checked_target_path(subdir, repo_id, repo_file.path)
        planned = PlannedDownload(repo_file=repo_file, target_rel=target_rel)
        target_abs = model_dir / target_rel
        entry = recorded.get(target_rel)
        if entry is None:
            if target_abs.exists():
                adopted.append(_reconcile_unrecorded_file(planned, target_abs, commit))
            else:
                to_download.append(planned)
            continue
        on_disk = target_abs.is_file()
        refreshable = refresh_docs and is_doc_file(repo_file.path)
        if repo_file.sha256 is not None and entry.sha256 is not None:
            if entry.sha256.lower() != repo_file.sha256.lower():
                if refreshable:
                    logger.info("replacing changed doc %s (--refresh-docs)", target_rel)
                    to_download.append(replace(planned, replace_existing=on_disk))
                    continue
                raise _immutability_stop(
                    target_rel,
                    f"changed upstream: archived sha256 {entry.sha256} differs from "
                    f"hub-declared sha256 {repo_file.sha256}",
                    repo_file.path,
                )
            if on_disk:
                logger.info("already archived: %s (hub hash matches the record)", target_rel)
                continue
        elif entry.size == repo_file.size:
            # Best effort without a hub hash: name + size match.
            if on_disk:
                logger.info(
                    "already archived: %s (name and size match; hub publishes no hash)", target_rel
                )
                continue
        else:
            if refreshable:
                logger.info("replacing changed doc %s (--refresh-docs)", target_rel)
                to_download.append(replace(planned, replace_existing=on_disk))
                continue
            raise _immutability_stop(
                target_rel,
                f"is recorded with size {entry.size} but the hub reports size "
                f"{repo_file.size} and publishes no hash to compare",
                repo_file.path,
            )
        # Recorded and content-matched, but the file is gone: the
        # archive was damaged outside the tool. Re-download rather than
        # trust the stale record; full drift detection is verify's job.
        logger.warning(
            "recorded file %s is missing on disk (archive drift); re-downloading", target_rel
        )
        to_download.append(planned)
    return PullPlan(to_download=to_download, adopted=adopted)
