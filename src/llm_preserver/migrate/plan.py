"""Deriving the migration plan from disk (spec 0017, criteria 9/10/13).

The plan is a walk of ``models/*/*/model-record.json`` comparing each
artifact's ``source_repo`` to the record's own ``hub_id``. Nothing is
stored: re-running derives the same conclusions from whatever state the
last run left, which is what makes migration resumable with no journal
to lose.

Two refusals matter more than they look. A whole-run refusal (rather
than skipping the bad directory) keeps the archive from ending up half
converted for a reason the human never saw; and a plan that promises a
directory removal it cannot honor would make ``os.rmdir`` the thing
that fails mid-run, after files have already moved.
"""

from pathlib import Path

from pydantic import ValidationError

from llm_preserver.archive import iter_model_dirs
from llm_preserver.layout import model_dir_for, repo_id_from_url, split_repo_id
from llm_preserver.migrate.models import (
    ArtifactMove,
    DirectoryMigration,
    MigrateError,
    MigratePlan,
    MigrateUserError,
)
from llm_preserver.records import (
    RECORD_FILENAME,
    TOOL_OWNED_ROOT_FILENAMES,
    ArtifactEntry,
    ModelRecord,
    load_record,
)
from llm_preserver.remove.models import escapes_model_dir, reached_through_symlink

MODELS_DIRNAME = "models"


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


def _check_movable(model_dir: Path, model_id: str, rel_paths: list[str]) -> None:
    """Refuse recorded paths that would move data out of the archive."""
    for rel_path in rel_paths:
        if escapes_model_dir(model_dir, rel_path):
            raise MigrateError(
                f"{model_id} records {rel_path!r}, which is a symlink or resolves outside "
                "its own directory; migration will not follow it"
            )


def _emptied_dirs(model_dir: Path, moving: set[str], staying: set[str]) -> list[Path]:
    """Directories the run may ``os.rmdir`` once the moves are done.

    Candidates come from the *recorded* paths, not from what is on disk:
    every ancestor directory of every moving file, up to the model
    directory. Deriving from the record is what makes a resumed run work
    — after a crash the files are already at their target, so a
    disk-driven walk would not see that ``gguf/`` was this migration's
    to clear.

    A candidate is kept when nothing on disk beneath it is staying or
    unrecorded, and the list is ordered **deepest first** so a child is
    always removed before its parent.

    Both properties were missing until live use found them
    (2026-08-11): the real archive nests docs at
    ``gguf/docs/<publisher>--<repo>/``, so listing only the directories
    that *directly* held a moving file skipped the intermediate
    ``gguf/docs`` and left ``gguf`` un-removable, while listing a parent
    first made its ``os.rmdir`` fail. Either way the preview promised a
    removal the run could not keep.
    """
    candidates: set[Path] = set()
    for rel in moving:
        parent = (model_dir / rel).parent
        while parent != model_dir and model_dir in parent.parents:
            candidates.add(parent)
            parent = parent.parent
    removals = [
        directory
        for directory in sorted(candidates, key=lambda p: len(p.parts), reverse=True)
        if _holds_nothing_staying(directory, moving, model_dir)
    ]
    if not staying and _model_dir_drains(model_dir, moving):
        removals.append(model_dir)
    return removals


def _holds_nothing_staying(directory: Path, moving: set[str], model_dir: Path) -> bool:
    """True when every file still under ``directory`` is one that leaves.

    Vacuously true for a directory already emptied by an interrupted
    run — which is the point: that one is this migration's to clear.
    """
    if not directory.is_dir():
        return False
    for entry in directory.rglob("*"):
        if entry.is_dir():
            continue
        if str(entry.relative_to(model_dir)) not in moving:
            return False
    return True


def _model_dir_drains(model_dir: Path, moving: set[str]) -> bool:
    """True when nothing but tool-owned files would remain at the root."""
    for entry in model_dir.rglob("*"):
        if entry.is_dir():
            continue
        rel = str(entry.relative_to(model_dir))
        if rel in moving or rel in TOOL_OWNED_ROOT_FILENAMES:
            continue
        return False
    return True


def plan_migration(root: Path, repos: list[str] | None = None) -> MigratePlan:
    """Derive the whole conversion from what is on disk.

    Args:
        root: Archive root.
        repos: Optional ``<owner>/<repo>`` filter — only these source
            directories are planned. Used by ``--repo``.

    Returns:
        The plan, empty when the archive already obeys ADR 0003.

    Raises:
        MigrateError: On any state migration refuses to act on. The
            whole run refuses; nothing is planned around a bad
            directory.
        MigrateUserError: If ``repos`` names a directory the archive
            does not hold — a silent empty plan would read as "already
            migrated" and hide the typo.
    """
    directories = iter_model_dirs(root)
    scope = _validated_scope(repos, [model_id for model_id, _ in directories], root)
    plan = MigratePlan()
    for model_id, model_dir in directories:
        if scope is not None and model_id not in scope:
            continue
        unit = _plan_directory(root, model_id, model_dir, plan)
        if unit is not None:
            plan.units.append(unit)
    plan.units.sort(key=lambda unit: unit.model_id)
    _add_emptied_owner_dirs(plan)
    return plan


def _add_emptied_owner_dirs(plan: MigratePlan) -> None:
    """Append owner directories every one of whose models is leaving.

    Decided once, across the whole plan, because emptiness is not a
    per-unit fact: two renames sharing an owner each saw a sibling still
    present and neither claimed the parent, so it survived empty and the
    preview's promise went unkept (review, 2026-08-11).
    """
    leaving: dict[Path, set[Path]] = {}
    for unit in plan.units:
        if unit.model_dir in unit.removed_dirs:
            leaving.setdefault(unit.model_dir.parent, set()).add(unit.model_dir)
    for owner_dir, going in leaving.items():
        try:
            present = {entry for entry in owner_dir.iterdir() if entry.is_dir()}
        except OSError:
            continue
        if present <= going:
            plan.units[-1].removed_dirs.append(owner_dir)


def _validated_scope(repos: list[str] | None, present: list[str], root: Path) -> set[str] | None:
    """Turn the ``--repo`` filter into a scope, refusing unknown ids."""
    if not repos:
        return None
    missing = sorted(set(repos) - set(present))
    if missing:
        raise MigrateUserError(f"no model directory for {', '.join(missing)} in {root}")
    return set(repos)


def _plan_directory(
    root: Path, model_id: str, model_dir: Path, plan: MigratePlan
) -> DirectoryMigration | None:
    """Plan one directory, or return None when nothing is owed from it.

    A directory with *no* record contributes nothing to plan and is not
    an error: it is what an interrupted run leaves at a target (files
    moved, record not yet written), and the source record that still
    lists those files is what drives the resumed move. An *unreadable*
    record is the opposite case and refuses the run.
    """
    if not (model_dir / RECORD_FILENAME).is_file():
        return None
    try:
        owner, repo = split_repo_id(model_id)
    except ValueError as exc:
        # A hand-made directory whose name is not a repo id: refuse by
        # name rather than tracebacking out of the CLI's handlers.
        raise MigrateError(
            f"{model_id} is not a usable <owner>/<repo> directory name ({exc}); "
            "rename or move it before migrating"
        ) from exc
    if reached_through_symlink(root / MODELS_DIRNAME, owner, repo):
        raise MigrateError(
            f"{model_id} is reached through a symlink; migration will not move files through it"
        )
    record = _load_or_refuse(model_dir, model_id)
    moves: list[ArtifactMove] = []
    moving: set[str] = set()
    staying: set[str] = set()
    for artifact in record.artifacts:
        rel_paths = [entry.path for entry in artifact.files]
        target_repo = _target_repo(artifact, model_id)
        if target_repo is None:
            staying.update(rel_paths)
            continue
        _check_movable(model_dir, model_id, rel_paths)
        target_dir = model_dir_for(root, target_repo)
        _check_target(target_dir, target_repo, model_dir, rel_paths)
        moving.update(rel_paths)
        moves.append(
            ArtifactMove(
                repo_id=target_repo,
                target_dir=target_dir,
                files=list(rel_paths),
                total_size=sum(entry.size or 0 for entry in artifact.files),
            )
        )
    if not moves:
        return None
    removals = _emptied_dirs(model_dir, moving, staying)
    if not staying and model_dir not in removals:
        plan.warnings.append(
            f"{model_id} keeps files nothing recorded, so its directory will remain after the move"
        )
    return DirectoryMigration(
        model_id=model_id,
        model_dir=model_dir,
        kind="rename" if not staying else "split",
        moves=moves,
        removed_dirs=removals,
    )


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
