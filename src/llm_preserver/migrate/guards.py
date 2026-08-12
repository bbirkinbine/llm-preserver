"""What a migration plan refuses to act on (spec 0017).

Split from ``plan.py`` at the 300-line cap. One concept: every state
the *planner* can see and must reject, so the executor never discovers
a problem after payload has already moved. Both reviewers converged on
that as the property `migrate` was designed around and did not keep
(2026-08-11), and ``docs/cli.md`` promises it as "the run refuses as a
whole — never half-converts".
"""

from pathlib import Path

from pydantic import ValidationError

from llm_preserver.layout import repo_id_from_url
from llm_preserver.migrate.models import MigrateError
from llm_preserver.records import ArtifactEntry, ModelRecord, load_record
from llm_preserver.remove.models import escapes_model_dir


def _load_or_refuse(model_dir: Path, model_id: str) -> ModelRecord:
    """Load a record, or refuse the whole run naming the directory."""
    try:
        return load_record(model_dir)
    except (ValidationError, ValueError, OSError) as exc:
        raise MigrateError(
            f"cannot read the record in {model_id} ({exc}); "
            "fix or move that directory before migrating"
        ) from exc


def _target_repo(artifact: ArtifactEntry, model_id: str) -> str | None:
    """The repo id an artifact's files belong to, or None when it is home.

    Raises:
        MigrateError: If the artifact records no usable source. Moving
            files needs a claim about where they came from; absent or
            unreadable, the tool will not guess (spec 0017 open
            question 3 — refuse and name the directory).
    """
    if artifact.source_repo is None:
        raise MigrateError(
            f"{model_id} has a {artifact.format} artifact with no recorded source_repo; "
            "migration cannot tell which repo its files belong to — "
            "record a source or remove the artifact before migrating"
        )
    repo_id = repo_id_from_url(artifact.source_repo)
    if repo_id is None:
        raise MigrateError(
            f"{model_id} records source_repo {artifact.source_repo!r}, "
            "which is not a hub repo URL this tool can resolve to a directory; "
            "fix the record before migrating"
        )
    return None if repo_id == model_id else repo_id


def _check_movable(model_dir: Path, model_id: str, rel_paths: list[str], target_dir: Path) -> None:
    """Refuse recorded paths the run could not move safely.

    Everything a plan can see, it refuses *here* — the executor
    discovering any of this would mean payload had already moved, and
    ``docs/cli.md`` promises the run refuses as a whole.
    """
    for rel_path in rel_paths:
        if escapes_model_dir(model_dir, rel_path):
            raise MigrateError(
                f"{model_id} records {rel_path!r}, which is a symlink or resolves outside "
                "its own directory; migration will not follow it"
            )
        if not (model_dir / rel_path).exists() and not (target_dir / rel_path).exists():
            # Absent from *both* sides is a genuinely missing file — an
            # interrupted pull, which `verify` calls `incomplete`.
            # Absent from the source but present at the target is an
            # interrupted *migration*, which is the resumable case and
            # must not be refused.
            raise MigrateError(
                f"{model_id} records {rel_path!r}, which is not on disk; "
                "run 'verify' and finish or discard that pull before migrating"
            )


def _check_distinct_target(model_dir: Path, model_id: str, target_dir: Path, repo: str) -> None:
    """Refuse a target that is really the source under another spelling.

    macOS and Windows filesystems are case-insensitive by default, so
    ``unsloth/Tiny-Chat-GGUF`` and ``unsloth/tiny-chat-GGUF`` are one
    directory. Every move would be a rename onto itself and the
    source-record cleanup would then delete the only copy of the
    hashes, license and provenance — reporting success (review,
    2026-08-11).
    """
    # samefile, not resolve(): macOS does not normalise case in a
    # resolved path, so two spellings of one directory compare unequal
    # as strings while being the same inode.
    try:
        same = target_dir.exists() and model_dir.samefile(target_dir)
    except OSError:
        same = False
    if same:
        raise MigrateError(
            f"{model_id} and {repo} are the same directory on this filesystem "
            "(they differ only in case); rename one by hand before migrating"
        )


def _check_paths_distinct(record: ModelRecord, model_id: str) -> None:
    """Refuse a record where two artifacts claim the same file.

    Whichever moved first would win and the other's move would silently
    vanish, or the file would end up recorded in two directories.

    Raises:
        MigrateError: Naming the contested path.
    """
    seen: set[str] = set()
    for artifact in record.artifacts:
        for entry in artifact.files:
            if entry.path in seen:
                raise MigrateError(
                    f"{model_id} records {entry.path!r} under more than one artifact; "
                    "migration cannot tell which repo it belongs to — fix the record first"
                )
            seen.add(entry.path)


def _check_target(
    target_dir: Path, target_repo: str, model_dir: Path, rel_paths: list[str]
) -> None:
    """Refuse a destination that is unsafe or already holds other bytes.

    A file already at the target with the *same recorded size* is a
    resumed move, not a collision — the previous run moved it and
    stopped before rewriting the records. Size, never a re-hash: the
    no-re-hash promise is what makes migration minutes rather than
    hours at archive scale.
    """
    if target_dir.is_symlink() or target_dir.parent.is_symlink():
        raise MigrateError(
            f"the destination for {target_repo} is reached through a symlink; refusing to write "
            "outside the archive"
        )
    for rel_path in rel_paths:
        existing = target_dir / rel_path
        if not existing.exists():
            continue
        source = model_dir / rel_path
        if not source.exists():
            continue  # already moved by an interrupted run
        if existing.stat().st_size != source.stat().st_size:
            raise MigrateError(
                f"{target_dir / rel_path} already exists with different contents; "
                "migration will not overwrite it — resolve it by hand and re-run"
            )
