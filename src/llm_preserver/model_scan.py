"""Shared on-disk scans of the archive against its records and layout.

Extracted from ``verify`` (spec 0009) when ``remove`` (spec 0010)
needed the identical unrecorded-file scan — the two commands must
never disagree about what "unrecorded" means. Spec 0012 adds the
hash-free ``.staging/`` leftover scan here for the same reason: verify
and any future caller share one definition of an abandoned download.
"""

from dataclasses import dataclass
from pathlib import Path

from llm_preserver.archive import ArchiveError
from llm_preserver.pull_prepare import STAGING_DIRNAME
from llm_preserver.records import TOOL_OWNED_ROOT_FILENAMES, ModelRecord


@dataclass(frozen=True)
class StagingLeftover:
    """One abandoned download found under ``.staging/`` (spec 0012).

    Attributes:
        model_id: ``<creator>/<model>`` the interrupted pull targeted,
            read from the staging directory layout.
        path: The ``.staging/<creator>/<model>/`` directory on disk.
        total_bytes: Sum of every regular (non-symlink) file beneath it.
        file_count: How many regular files it holds.
    """

    model_id: str
    path: Path
    total_bytes: int
    file_count: int


def unrecorded_files(model_dir: Path, record: ModelRecord) -> list[str]:
    """On-disk files no record lists, exempting tool-owned generated files.

    Args:
        model_dir: The model directory (``models/<creator>/<model>``).
        record: The model's validated record.

    Returns:
        Sorted model-dir-relative POSIX paths of regular files present
        on disk but absent from the record. Symlinks are skipped, and
        the tool-owned root files (record, rendering, manifest) are
        exempt.
    """
    recorded = {entry.path for artifact in record.artifacts for entry in artifact.files}
    found = []
    for path in model_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(model_dir).as_posix()
        if rel not in recorded and rel not in TOOL_OWNED_ROOT_FILENAMES:
            found.append(rel)
    return sorted(found)


def _leaf_size_and_count(leaf: Path) -> tuple[int, int]:
    """Sum bytes and count regular files beneath a staging leaf.

    ``rglob`` on 3.12 does not descend symlinked directories, and a
    symlinked file reports ``is_file()`` True — so both are excluded
    here, keeping the sum inside the archive tree (same posture as
    ``unrecorded_files``). A single unreadable entry (a NAS ``ESTALE``
    race, a foreign-uid file in a copied archive) is skipped rather
    than aborting the whole scan — verify degrades on per-file I/O the
    same way, and this is a best-effort informational count.
    """
    total = 0
    count = 0
    for path in leaf.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            total += path.stat().st_size
        except OSError:
            continue
        count += 1
    return total, count


def staging_leftovers(root: Path) -> list[StagingLeftover]:
    """Abandoned downloads left under ``.staging/`` (spec 0012).

    A pull stages into ``.staging/<creator>/<model>/`` and only deletes
    it once the files have moved into ``models/`` and the record is
    written; an interrupted pull leaves that directory behind. This is a
    pure directory scan — no record load, no ``models/`` walk, no
    hashing — so finding leftovers never costs a hash run.

    Args:
        root: The archive root.

    Returns:
        One :class:`StagingLeftover` per ``.staging/<creator>/<model>/``
        directory that holds at least one regular file, sorted by
        ``model_id``. An empty directory (no regular file) is not a
        leftover.

    Raises:
        ArchiveError: If ``.staging/`` is itself a symlink — it could
            point anywhere on the host, the same refusal
            ``iter_model_dirs`` applies to ``models/``.
        OSError: If the staging tree cannot be listed (an unreadable
            ``.staging/`` directory). Callers map this to a clean exit
            rather than letting it crash — a leftover scan must not
            traceback on a foreign-uid or read-blocked archive.
    """
    staging_root = root / STAGING_DIRNAME
    if staging_root.is_symlink():
        raise ArchiveError(f"{staging_root} is a symlink; refusing to walk it")
    if not staging_root.is_dir():
        return []
    leftovers: list[StagingLeftover] = []
    # Skip any creator or leaf reached through a symlink: following one
    # would let the scan (and the byte sum) escape the archive tree.
    for creator_dir in staging_root.iterdir():
        if not creator_dir.is_dir() or creator_dir.is_symlink():
            continue
        for leaf in creator_dir.iterdir():
            if not leaf.is_dir() or leaf.is_symlink():
                continue
            total, count = _leaf_size_and_count(leaf)
            if count == 0:
                continue
            leftovers.append(
                StagingLeftover(
                    model_id=f"{creator_dir.name}/{leaf.name}",
                    path=leaf,
                    total_bytes=total,
                    file_count=count,
                )
            )
    return sorted(leftovers, key=lambda left: left.model_id)
