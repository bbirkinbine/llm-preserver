"""Applying a migration plan (spec 0017, criteria 10/13/14/17).

Ordering is the whole design. Each unit does, in order: move the
artifact's files, write the target's manifest then its record, rewrite
the source record without the departed artifact, then ``os.rmdir`` what
drained. Record-last is ADR 0001's convention, and here it is also what
makes the run resumable — a crash leaves payload the next
``plan_migration`` still sees and re-plans, because the plan is derived
from records and disk rather than from a journal.

Nothing is re-hashed. A file's path *inside* its artifact does not
change, so every recorded digest stays true and the manifests are
regenerated from the record rather than from the bytes.
"""

import errno
from contextlib import suppress
from pathlib import Path

from llm_preserver.archive import ArchiveError
from llm_preserver.file_locks import clear_immutable, immutable_flags, restore_immutable
from llm_preserver.migrate.models import (
    DirectoryMigration,
    MigrateError,
    MigrateEvents,
    MigratePlan,
)
from llm_preserver.pull_record import write_manifest
from llm_preserver.records import (
    RECORD_FILENAME,
    TOOL_OWNED_ROOT_FILENAMES,
    ArtifactEntry,
    ModelRecord,
    load_record,
    save_record,
)


def execute_migration(root: Path, plan: MigratePlan, events: MigrateEvents | None = None) -> None:
    """Apply every unit of ``plan`` to the archive at ``root``.

    Args:
        root: Archive root.
        plan: A plan from ``plan_migration`` against this same archive.

    Raises:
        MigrateError: On a cross-filesystem move, or an OS failure
            partway. Whatever completed stays completed and re-running
            resumes — the plan is re-derived from disk.
    """
    events = events or MigrateEvents()
    scoped = plan.scoped
    for unit in plan.units:
        if events.on_directory_start is not None:
            files = sum(len(move.files) for move in unit.moves)
            events.on_directory_start(unit.model_id, files, sum(m.total_size for m in unit.moves))
        _migrate_directory(unit, events)
    if not scoped:
        _flip_marker_when_converted(root)


def _flip_marker_when_converted(root: Path) -> None:
    """Raise the archive marker once nothing is left unmigrated.

    Criterion 24: the flip is the single durable signal that migration
    finished, so it happens only when a fresh scan finds no directory
    still holding another repo's files — never after a ``--repo`` run,
    which converts part of an archive and must not claim the whole one
    is done.
    """
    from llm_preserver.archive import SCHEMA_VERSION, set_schema_version
    from llm_preserver.layout import unmigrated_directories

    if unmigrated_directories(root):
        return
    with suppress(ArchiveError):
        set_schema_version(root, SCHEMA_VERSION)


def _migrate_directory(unit: DirectoryMigration, events: MigrateEvents) -> None:
    """Move one directory's foreign artifacts out and fix both records."""
    record = load_record(unit.model_dir)
    moved_repos = {move.repo_id for move in unit.moves}
    for move in unit.moves:
        for rel_path in move.files:
            if events.on_file is not None:
                events.on_file(rel_path, 0)
            _move_file(unit.model_dir / rel_path, move.target_dir / rel_path)
        artifacts = [
            artifact
            for artifact in record.artifacts
            if _artifact_repo(artifact) == move.repo_id
            and {entry.path for entry in artifact.files} <= set(move.files)
        ]
        _write_target_record(move.target_dir, move.repo_id, unit.model_id, artifacts, record)
    _rewrite_source_record(unit, record, moved_repos)
    for directory in unit.removed_dirs:
        _rmdir_if_empty(directory)


def _artifact_repo(artifact: ArtifactEntry) -> str | None:
    """The artifact's source repo id, or None when unrecorded."""
    from llm_preserver.layout import repo_id_from_url

    return repo_id_from_url(artifact.source_repo)


def _move_file(source: Path, target: Path) -> None:
    """Rename one file into its publisher's directory.

    **Unlocks around the move.** ADR 0001 locks payload after download,
    and on an SMB share that lock comes back as the BSD immutable flag
    (the client stores the mode as the DOS read-only attribute and
    surfaces it as ``UF_IMMUTABLE``); ``rename(2)`` on an immutable file
    fails with EPERM. Found in live use on the real NAS, where it
    stopped migration on the first file. The flag is cleared for the
    length of the rename and restored on the moved file, mirroring the
    unlock/relock ``pull_transfer`` already does for the write bit — the
    lock is borrowed, never spent.

    Raises:
        MigrateError: If the move would cross filesystems, or fails for
            any other reason. Never a silent copy-and-delete: at archive
            scale that turns a minutes-long rename into an hours-long
            copy without saying so, and ``--to`` is the mode that copies
            on purpose.
    """
    if not source.exists() and target.exists():
        return  # an interrupted run already moved it
    target.parent.mkdir(parents=True, exist_ok=True)
    locked = immutable_flags(source)
    if locked:
        try:
            clear_immutable(source)
        except OSError as exc:
            raise MigrateError(
                f"cannot unlock {source} to move it ({exc}); the archive's payload lock is "
                "held by a file flag this user cannot clear"
            ) from exc
    try:
        # Path.rename, not shutil.move: a rename that would cross
        # filesystems must fail loudly rather than degrade into a copy.
        source.rename(target)
    except OSError as exc:
        restore_immutable(source, locked)  # leave the source as we found it
        if exc.errno == errno.EXDEV:
            raise MigrateError(
                f"{source} and {target} are on different filesystems, so this move would be a "
                "copy-and-delete rather than a rename; use --to <root> to write a converted "
                "copy instead"
            ) from exc
        raise MigrateError(f"cannot move {source} to {target}: {exc}") from exc
    restore_immutable(target, locked)


def _write_target_record(
    target_dir: Path,
    repo_id: str,
    source_model_id: str,
    artifacts: list[ArtifactEntry],
    source_record: ModelRecord,
) -> None:
    """Create or extend the publisher's own record.

    The directory the files are leaving is the only place a curator's
    confirmed "this repo converts that model" judgment survives, so it
    becomes the new record's ``base_model`` (criterion 14) attributed
    as ``migrated`` — never as though a card had declared it.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    if (target_dir / RECORD_FILENAME).is_file():
        record = load_record(target_dir)
        record.artifacts = _merged_artifacts(record.artifacts, artifacts)
    else:
        # A copy of the source record, not five fields off it: the
        # directory it leaves is about to be deleted, so anything not
        # carried is destroyed. `pipeline_tag` was lost on all three
        # renames of the live archive this way, and `notes` — free-form
        # curator text — would have been unrecoverable (review,
        # 2026-08-11).
        record = source_record.model_copy(deep=True)
        record.name = repo_id.split("/")[-1]
        record.hub_id = repo_id
        record.artifacts = list(artifacts)
    if record.base_model is None and source_model_id != repo_id:
        record.base_model = source_model_id
        record.base_model_source = "migrated"
    _commit_record(target_dir, record)


def _merged_artifacts(
    existing: list[ArtifactEntry], incoming: list[ArtifactEntry]
) -> list[ArtifactEntry]:
    """Fold arriving artifacts into a target record without duplicating.

    Keyed on ``(format, source_repo)`` — the same key ``update_record``
    uses — because a resumed run re-plans a move whose target record was
    already written. Appending blindly produced two identical artifacts
    listing one file: `verify` calls that `valid` (each entry hashes
    fine) and `status` doubles the size, so nothing would ever surface
    it (review, 2026-08-11).
    """
    merged = list(existing)
    for artifact in incoming:
        key = (artifact.format, artifact.source_repo)
        match = next(
            (a for a in merged if (a.format, a.source_repo) == key),
            None,
        )
        if match is None:
            merged.append(artifact)
            continue
        by_path = {entry.path: index for index, entry in enumerate(match.files)}
        for entry in artifact.files:
            if entry.path in by_path:
                match.files[by_path[entry.path]] = entry
            else:
                match.files.append(entry)
    return merged


def _rewrite_source_record(
    unit: DirectoryMigration, record: ModelRecord, moved_repos: set[str]
) -> None:
    """Drop the departed artifacts, or clear the directory entirely."""
    remaining = [
        artifact for artifact in record.artifacts if _artifact_repo(artifact) not in moved_repos
    ]
    if remaining:
        record.artifacts = remaining
        _commit_record(unit.model_dir, record)
        return
    if unit.model_dir in unit.removed_dirs:
        # A pure rename: the record moved with the files. What is left
        # here is only the tool's own generated paperwork (ADR 0001
        # classes it regenerable, never payload), and it has to go for
        # the directory to be removable.
        for name in TOOL_OWNED_ROOT_FILENAMES:
            (unit.model_dir / name).unlink(missing_ok=True)
        return
    record.artifacts = []
    _commit_record(unit.model_dir, record)


def _commit_record(model_dir: Path, record: ModelRecord) -> None:
    """Write the manifest, then the record — record last, as ever.

    ``write_manifest`` with no digest anticipates the exact bytes
    ``save_record`` writes next; reversing the two would leave a
    sidecar whose ``model-record.json`` line ``sha256sum -c`` rejects
    forever, and verify would call the record invalid.
    """
    try:
        write_manifest(model_dir, record)
        save_record(record, model_dir)
    except OSError as exc:
        # A read-only mount, a full disk, or metadata someone locked
        # beyond ADR 0001's model. A clean refusal, never a traceback:
        # the payload has already moved and the next run re-plans from
        # disk, so saying what failed is what the human needs.
        raise MigrateError(
            f"cannot write the record in {model_dir} ({exc}); the files moved, so re-run "
            "migrate once the directory is writable and it will finish"
        ) from exc


def _rmdir_if_empty(directory: Path) -> None:
    """Remove a drained directory, never recursively.

    ``os.rmdir`` fails rather than descending if anything at all
    remains, so a wrong plan cannot destroy content. Migration deletes
    no payload file: bytes leave a directory by being moved out of it.
    """
    try:
        directory.rmdir()
    except OSError:
        return
