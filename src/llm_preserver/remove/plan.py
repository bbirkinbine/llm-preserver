"""Planning half of managed removal (spec 0010): decide what to delete.

``plan_removal`` resolves the target, refuses symlink-escaping paths,
and builds a :class:`RemovePlan` for either whole-model or pattern-
scoped removal. It deletes nothing — every filesystem mutation lives in
``remove.execute``.
"""

import fnmatch
from collections.abc import Sequence
from pathlib import Path

from llm_preserver.archive import require_archive
from llm_preserver.model_scan import unrecorded_files
from llm_preserver.pull_prepare import STAGING_DIRNAME
from llm_preserver.records import (
    ID_COMPONENT_RE,
    RECORD_FILENAME,
    TOOL_OWNED_ROOT_FILENAMES,
    ModelRecord,
    load_record,
)
from llm_preserver.remove.models import (
    ModelNotFound,
    PlannedFile,
    RemoveError,
    RemovePlan,
    RemoveUserError,
    escapes_model_dir,
    reached_through_symlink,
)


def _try_load(model_dir: Path) -> ModelRecord | None:
    """Load a record, returning None when it is absent or unreadable."""
    if not (model_dir / RECORD_FILENAME).is_file():
        return None
    try:
        return load_record(model_dir)
    except Exception:
        # Any load/validation fault degrades to "no readable record":
        # a degraded model must still be removable (spec 0010). The
        # on-disk JSON is never touched here.
        return None


def plan_removal(root: Path, model_id: str, include: Sequence[str] | None) -> RemovePlan:
    """Plan a removal without deleting anything.

    Args:
        root: The archive root.
        model_id: The ``<creator>/<model>`` to remove (already shape-
            validated by the CLI).
        include: fnmatch patterns for a pattern-scoped removal, or None
            / empty for a whole-model removal.

    Returns:
        The removal plan.

    Raises:
        ArchiveError: If ``root`` is not an archive this tool may touch.
        RemoveError: If the model directory is reached through a
            symlink, or pattern removal targets a model with no readable
            record.
        RemoveUserError: If a pattern matches nothing, or matches every
            archived file.
        ModelNotFound: If neither a model directory nor staging
            leftovers exist for ``model_id``.
    """
    require_archive(root)
    creator, sep, name = model_id.partition("/")
    # Defense in depth: the CLI validates the id shape before calling,
    # but a direct core caller must not be able to address anything
    # outside models/ (``..``, empty component) via path construction.
    if not sep or not ID_COMPONENT_RE.fullmatch(creator) or not ID_COMPONENT_RE.fullmatch(name):
        raise RemoveError(f"model id must look like <creator>/<model>, got {model_id!r}")
    models_root = root / "models"
    staging_root = root / STAGING_DIRNAME
    model_dir = models_root / creator / name
    staging_dir = staging_root / creator / name

    # Refuse a model reached *through* a symlink at any level — the
    # container, the creator dir, or the leaf. A leaf-only check misses
    # ``models/<symlinked-creator>/<model>``, whose ``rmtree`` would
    # delete the symlink's target outside the archive. This is the same
    # posture ``iter_model_dirs`` enforces via ``_real_subdirs``.
    if reached_through_symlink(models_root, creator, name):
        raise RemoveError(f"{model_dir} is (or is reached through) a symlink; refusing to remove")

    model_present = model_dir.is_dir()
    staging_present = staging_dir.is_dir() and not reached_through_symlink(
        staging_root, creator, name
    )

    if not model_present and not staging_present:
        raise ModelNotFound(model_id)

    patterns = tuple(include or ())
    if patterns:
        if not model_present:
            raise RemoveError(
                f"{model_id} has no archived model directory; "
                f"pattern removal needs a readable record"
            )
        return _plan_pattern(model_id, model_dir, patterns)
    return _plan_whole(model_id, model_dir if model_present else None, staging_dir, staging_present)


def _plan_whole(
    model_id: str,
    model_dir: Path | None,
    staging_dir: Path,
    staging_present: bool,
) -> RemovePlan:
    """Plan a whole-model removal (record + payload + staging)."""
    files: list[PlannedFile] = []
    record_readable = False
    if model_dir is not None:
        record = _try_load(model_dir)
        if record is not None:
            record_readable = True
            files = [
                PlannedFile(entry.path, entry.size, unrecorded=False)
                for artifact in record.artifacts
                for entry in artifact.files
            ]
        else:
            # Degraded (no readable record): fall back to disk facts so
            # the model stays removable and the preview still has counts.
            files = _disk_payload(model_dir)
    return RemovePlan(
        model_id=model_id,
        whole_model=True,
        files=files,
        staging_dir=staging_dir if staging_present else None,
        model_dir=model_dir,
        record_readable=record_readable,
        total_size=sum(planned.size or 0 for planned in files),
    )


def _plan_pattern(model_id: str, model_dir: Path, patterns: tuple[str, ...]) -> RemovePlan:
    """Plan a pattern-scoped removal against the archived payload paths."""
    record = _try_load(model_dir)
    if record is None:
        raise RemoveError(
            f"{model_id} has no readable record; pattern removal needs one — use plain 'remove'"
        )
    recorded_paths = {entry.path for artifact in record.artifacts for entry in artifact.files}
    candidates: list[PlannedFile] = [
        PlannedFile(entry.path, entry.size, unrecorded=False)
        for artifact in record.artifacts
        for entry in artifact.files
    ]
    for rel in unrecorded_files(model_dir, record):
        # Only strays in a non-root subtree are pattern-removable —
        # a root-level file is not archived payload (it sits where the
        # tool-owned files live), so it stays unmatched (spec 0010).
        if "/" not in rel:
            continue
        candidates.append(PlannedFile(rel, (model_dir / rel).stat().st_size, unrecorded=True))

    matched = [
        planned
        for planned in candidates
        if any(fnmatch.fnmatch(planned.path, pattern) for pattern in patterns)
    ]
    if not matched:
        shown = ", ".join(repr(pattern) for pattern in patterns)
        raise RemoveUserError(f"no archived file matches {shown}")

    # A symlinked recorded payload file is refused (spec 0010). Pattern
    # mode drops matched entries from the record, so silently *skipping*
    # a symlink (never followed) would de-list a file that stays on disk
    # — a record/disk mismatch that a re-run cannot converge, since the
    # unrecorded scan skips symlinks. Refuse instead; whole-model remove
    # still clears such a model safely.
    for planned in matched:
        if not planned.unrecorded and escapes_model_dir(model_dir, planned.path):
            raise RemoveError(
                f"{model_dir / planned.path} is (or crosses) a symlink; refusing pattern "
                f"removal — use plain 'remove {model_id}' to clear the whole model"
            )

    matched_recorded = {planned.path for planned in matched if not planned.unrecorded}
    if recorded_paths and matched_recorded >= recorded_paths:
        raise RemoveUserError(
            f"pattern matches every archived file; "
            f"use plain 'remove {model_id}' to remove the whole model"
        )

    return RemovePlan(
        model_id=model_id,
        whole_model=False,
        files=matched,
        staging_dir=None,
        model_dir=model_dir,
        record_readable=True,
        total_size=sum(planned.size or 0 for planned in matched),
        include=patterns,
        record=record,
    )


def _disk_payload(model_dir: Path) -> list[PlannedFile]:
    """Every real payload file on disk, for a model with no record."""
    found: list[PlannedFile] = []
    for path in sorted(model_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(model_dir).as_posix()
        if rel in TOOL_OWNED_ROOT_FILENAMES:
            continue
        found.append(PlannedFile(rel, path.stat().st_size, unrecorded=True))
    return found
