"""Pull record-keeping: fold a pull into the record, write the manifest.

Split out of ``pull.py`` (300-line rule): this module owns what a
completed pull writes *about* the files — ``pull.py`` owns the files
themselves (stage, hash, verify, move).
"""

import datetime
import hashlib
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from llm_preserver.hub import RepoInfo
from llm_preserver.records import (
    MANIFEST_FILENAME,
    RECORD_FILENAME,
    ArtifactEntry,
    ArtifactFormat,
    FileEntry,
    ModelRecord,
    Role,
    derive_artifact_provenance,
)


def update_record(
    record: ModelRecord | None,
    info: RepoInfo,
    repo_id: str,
    creator: str,
    name: str,
    roles: list[Role],
    subdir: ArtifactFormat,
    new_entries: Sequence[FileEntry],
) -> ModelRecord:
    """Fold this pull's files into the one record that spans formats.

    Re-pulling updates the matching artifact (same format and source
    repo) in place — replacing re-downloaded file entries, keeping the
    rest — and never clobbers artifacts recorded by earlier pulls. A
    v1-era artifact of the same format with no ``source_repo`` is the
    merge target (the source is filled in), not a duplicate. Hub facts
    are never erased: ``pipeline_tag`` (like ``license``) only
    overwrites with a non-null value. ``artifact.revision`` becomes
    this pull's commit (most-recent-pull semantics); each file keeps
    its own ``FileEntry.revision`` pin.

    Args:
        record: The model's existing record, or None for a first pull.
        info: The repo metadata this pull resolved.
        repo_id: The hub repo the files came from.
        creator: Canonical model creator (directory segment).
        name: Canonical model name (directory segment).
        roles: Validated roles to assign; merged into existing roles.
        subdir: The format subdirectory the pull landed in.
        new_entries: File entries for everything this pull archived.

    Returns:
        The updated (or freshly built) record, ready to save.
    """
    source_repo = f"https://huggingface.co/{repo_id}"
    today = datetime.date.today()
    if record is None:
        record = ModelRecord(
            name=name,
            hub_id=f"{creator}/{name}",
            roles=roles,
            pipeline_tag=info.pipeline_tag,
            license=info.license,
        )
    else:
        if info.pipeline_tag is not None:
            record.pipeline_tag = info.pipeline_tag
        if record.license is None:
            record.license = info.license
        record.roles.extend(role for role in roles if role not in record.roles)
    artifact = next(
        (a for a in record.artifacts if a.format == subdir and a.source_repo == source_repo),
        None,
    )
    if artifact is None:
        # v1 records carry no source_repo; a same-format artifact with
        # source_repo None is this pull's own history, not a different
        # source — merge into it and fill the source in.
        artifact = next(
            (a for a in record.artifacts if a.format == subdir and a.source_repo is None),
            None,
        )
        if artifact is not None:
            artifact.source_repo = source_repo
    if artifact is None:
        artifact = ArtifactEntry(
            format=subdir,
            source_repo=source_repo,
            provenance="hashed-locally",  # recomputed below from the files
        )
        record.artifacts.append(artifact)
    by_path = {entry.path: index for index, entry in enumerate(artifact.files)}
    for entry in new_entries:
        if entry.path in by_path:
            artifact.files[by_path[entry.path]] = entry
        else:
            artifact.files.append(entry)
    artifact.revision = info.commit
    artifact.download_date = today
    artifact.provenance = derive_artifact_provenance(artifact.files)
    return record


def write_manifest(model_dir: Path, record: ModelRecord, record_sha256: str | None = None) -> None:
    """Write ``manifest-sha256.txt`` covering payload plus the record.

    ``sha256sum -c``-compatible lines for every hashed file entry, plus
    one for ``model-record.json`` itself. The write is atomic (tmp file
    then rename), so an interrupted run never leaves a partial sidecar.

    Args:
        model_dir: The model directory receiving the sidecar.
        record: The record whose hashed file entries become the lines.
        record_sha256: Digest for the ``model-record.json`` line. Pull
            omits it: the default hashes the exact serialization
            ``save_record`` writes next (the record stays the last
            write; the manifest anticipates it byte-for-byte). Verify
            (spec 0009) passes the digest of the record bytes already
            on disk — a loaded record's re-serialization need not be
            byte-identical to the file it came from, and a manifest
            line ``sha256sum -c`` rejects would defeat the sidecar.
    """
    if record_sha256 is None:
        record_json = record.model_dump_json(indent=2) + "\n"
        record_sha256 = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
    lines = [
        f"{entry.sha256}  {entry.path}"
        for artifact in record.artifacts
        for entry in artifact.files
        if entry.sha256 is not None
    ]
    lines.append(f"{record_sha256}  {RECORD_FILENAME}")
    # mkstemp opens O_CREAT|O_EXCL: a pre-planted symlink at a
    # predictable tmp name can never redirect this write outside the
    # archive (verify runs against archives the user did not author).
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=model_dir, prefix=MANIFEST_FILENAME + ".", suffix=".tmp"
    )
    manifest_tmp = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        manifest_tmp.replace(model_dir / MANIFEST_FILENAME)
    except BaseException:
        # Interrupt or write/rename fault: never strand tmp debris.
        manifest_tmp.unlink(missing_ok=True)
        raise
