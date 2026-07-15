"""Execution half of managed removal (spec 0010): carry out the plan.

The deletion ordering inverts ADR 0001's "source of truth last". Whole-
model deletes the record first, so a crash leaves an unrecorded
directory ``status``/``verify`` already surface — never a record naming
missing files. Pattern mode writes the *updated* record first, then
unlinks the de-listed files, so a crash leaves informational
``unrecorded`` strays a re-run sweeps up.
"""

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path

from llm_preserver.pull_record import write_manifest
from llm_preserver.records import RECORD_FILENAME, RENDERED_FILENAME, ModelRecord, save_record
from llm_preserver.remove.models import RemoveError, RemovePlan, escapes_model_dir


def execute_removal(
    root: Path,
    plan: RemovePlan,
    on_file: Callable[[str], None] | None = None,
) -> None:
    """Carry out a planned removal.

    Args:
        root: The archive root (unused directly; the plan carries
            resolved paths, kept for symmetry with ``plan_removal``).
        plan: The plan from ``plan_removal``.
        on_file: Optional callback fired with each deleted payload
            file's relative path — the CLI's per-file progress hook.
    """
    if plan.whole_model:
        _execute_whole(plan, on_file)
    else:
        _execute_pattern(plan, on_file)


def _execute_whole(plan: RemovePlan, on_file: Callable[[str], None] | None) -> None:
    """Delete record first, then payload, then staging (crash-safe order)."""
    model_dir = plan.model_dir
    if model_dir is not None:
        record_path = model_dir / RECORD_FILENAME
        if record_path.exists():
            record_path.unlink()  # source of truth first
        for planned in plan.files:
            if escapes_model_dir(model_dir, planned.path):
                continue  # never follow a symlink out of the model directory
            target = model_dir / planned.path
            if target.exists() or target.is_symlink():
                target.unlink()
            if on_file is not None:
                on_file(planned.path)
        # Sweep any remaining files (rendered markdown, manifest,
        # unrecorded strays) before removing the now-empty tree. rglob
        # does not descend symlinked directories and unlink removes the
        # link, not its target, so the sweep cannot escape either.
        for leftover in sorted(model_dir.rglob("*"), reverse=True):
            if leftover.is_file() or leftover.is_symlink():
                leftover.unlink()
        shutil.rmtree(model_dir, ignore_errors=True)
        _prune_empty_dir(model_dir.parent)
    if plan.staging_dir is not None and plan.staging_dir.exists():
        shutil.rmtree(plan.staging_dir)
        _prune_empty_dir(plan.staging_dir.parent)


def _execute_pattern(plan: RemovePlan, on_file: Callable[[str], None] | None) -> None:
    """Update the record first, then unlink the de-listed files."""
    model_dir = plan.model_dir
    record = plan.record
    if model_dir is None or record is None:  # guaranteed by _plan_pattern
        raise RemoveError("internal: pattern removal plan is missing its model directory or record")
    matched_recorded = {planned.path for planned in plan.files if not planned.unrecorded}
    survivor = _survivor_record(record, matched_recorded)

    # ``save_record`` writes MODEL-RECORD.md and model-record.json with
    # plain ``write_text``, which follows a symlink at the destination.
    # A copied archive could plant either as a symlink pointing outside
    # the tree, turning the rewrite into an arbitrary out-of-tree write.
    # (``model-record.json`` is already refused at plan time by
    # ``load_record``; MODEL-RECORD.md has no such gate.)
    for owned in (RENDERED_FILENAME, RECORD_FILENAME):
        if (model_dir / owned).is_symlink():
            raise RemoveError(f"{model_dir / owned} is a symlink; refusing to rewrite through it")

    save_record(survivor, model_dir)  # JSON + markdown: source of truth updated first
    disk_record_sha256 = hashlib.sha256((model_dir / RECORD_FILENAME).read_bytes()).hexdigest()
    write_manifest(model_dir, survivor, record_sha256=disk_record_sha256)

    for planned in plan.files:
        if escapes_model_dir(model_dir, planned.path):
            continue  # never follow a symlink out of the model directory
        target = model_dir / planned.path
        if target.exists() or target.is_symlink():
            target.unlink()
        if on_file is not None:
            on_file(planned.path)
    _prune_empty_subdirs(model_dir)


def _survivor_record(record: ModelRecord, removed_paths: set[str]) -> ModelRecord:
    """Return a copy of ``record`` without the removed files or emptied artifacts."""
    kept_artifacts = []
    for artifact in record.artifacts:
        kept_files = [entry for entry in artifact.files if entry.path not in removed_paths]
        if not kept_files:
            continue  # an emptied artifact is dropped, not left as a husk
        kept_artifacts.append(artifact.model_copy(update={"files": kept_files}))
    return record.model_copy(update={"artifacts": kept_artifacts})


def _prune_empty_subdirs(model_dir: Path) -> None:
    """Remove now-empty subdirectories of a model dir (kept format dirs)."""
    for path in sorted(model_dir.rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            path.rmdir()


def _prune_empty_dir(directory: Path) -> None:
    """Remove ``directory`` (a creator dir) only if it is now empty."""
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass
