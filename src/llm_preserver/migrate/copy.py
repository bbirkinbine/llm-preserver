"""``migrate --to``: a converted copy, leaving the source untouched.

The reversible mode. Bytes are copied, never hardlinked: two trees
sharing inodes would cut against ADR 0001's immutable-payload
separation, and a rehearsal whose payload is the original's payload is
not a rehearsal. ``--repo`` narrows the copy so trying the conversion
on one model costs minutes instead of a whole-archive transfer.
"""

import shutil
from pathlib import Path

from llm_preserver.archive import ArchiveError, init_archive, is_archive
from llm_preserver.layout import model_dir_for, split_repo_id
from llm_preserver.migrate.execute import execute_migration
from llm_preserver.migrate.models import MigrateEvents, MigrateUserError
from llm_preserver.migrate.plan import plan_migration


def copy_migration(
    root: Path,
    dest: Path,
    repos: list[str] | None = None,
    events: MigrateEvents | None = None,
) -> None:
    """Write a converted copy of ``root`` (or part of it) at ``dest``.

    Args:
        root: Source archive, left untouched.
        dest: Destination root, created if absent.
        repos: Optional ``<owner>/<repo>`` filter.

    Raises:
        MigrateUserError: If ``dest`` cannot be used, or a named repo
            has no directory in the source.
    """
    events = events or MigrateEvents()
    _prepare_dest(dest)
    for model_id in _selected(root, repos):
        owner, repo = split_repo_id(model_id)
        source_dir = model_dir_for(root, model_id)
        if events.on_directory_start is not None:
            payload = [path for path in source_dir.rglob("*") if path.is_file()]
            events.on_directory_start(
                model_id, len(payload), sum(path.stat().st_size for path in payload)
            )
        _copy_model_dir(source_dir, dest / "models" / owner / repo, events)
    execute_migration(dest, plan_migration(dest), events)


def _copy_model_dir(source_dir: Path, target_dir: Path, events: MigrateEvents) -> None:
    """Copy one model directory, resuming rather than refusing.

    ``dirs_exist_ok`` and the same-size skip exist because of a live
    failure (2026-08-11): the copy of a 16 GiB model took ten minutes,
    the conversion inside it then failed, and the retry died with an
    unhandled ``FileExistsError`` from ``copytree``. A rehearsal that
    cannot be re-run is not a rehearsal — and re-paying ten minutes to
    copy bytes already on disk is its own reason to skip them.

    Size, not hash: nothing here re-reads payload, which is the same
    promise the in-place path makes.
    """

    def copy_unless_present(src: str, dst: str, *, follow_symlinks: bool = True) -> object:
        source, target = Path(src), Path(dst)
        size = source.stat().st_size
        if target.exists() and target.stat().st_size == size:
            return dst
        if events.on_file is not None:
            # Announced *before* the copy: a multi-gigabyte file is
            # exactly where a human needs to know something is happening.
            events.on_file(source.name, size)
        return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

    shutil.copytree(
        source_dir,
        target_dir,
        symlinks=False,
        dirs_exist_ok=True,
        copy_function=copy_unless_present,
    )


def _prepare_dest(dest: Path) -> None:
    """Make ``dest`` an archive, refusing a non-empty foreign directory."""
    try:
        init_archive(dest)
    except ArchiveError as exc:
        raise MigrateUserError(f"cannot use {dest} as the destination: {exc}") from exc
    if not is_archive(dest):  # pragma: no cover - init_archive raises first
        raise MigrateUserError(f"{dest} is not an archive")


def _selected(root: Path, repos: list[str] | None) -> list[str]:
    """Model ids to copy: the named ones, or every directory."""
    from llm_preserver.archive import iter_model_dirs

    present = [model_id for model_id, _ in iter_model_dirs(root)]
    if not repos:
        return present
    missing = [repo for repo in repos if repo not in present]
    if missing:
        raise MigrateUserError(f"no model directory for {', '.join(sorted(missing))} in {root}")
    return [model_id for model_id in present if model_id in repos]
