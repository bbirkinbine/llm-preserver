"""Disk-space preflight for whole-tree pulls (spec 0004).

Whole-tree snapshots are routinely 50-500GB; file sizes are already in
the one metadata call, so the tool sums them, shows the total in the
confirmation prompt, and refuses (local-environment domain) before any
bytes download when the tree will not fit at the archive path.
"""

import shutil
from collections.abc import Sequence
from pathlib import Path

from llm_preserver.hub import PullEnvError, RepoFile
from llm_preserver.pull_plan import PlannedDownload

_BINARY_UNITS = ("KiB", "MiB", "GiB", "TiB", "PiB")


def total_selected_size(files: Sequence[RepoFile]) -> tuple[int, int]:
    """Sum hub-reported sizes over a selection.

    Files the hub reports no size for are excluded from the byte total
    but still counted — the confirmation shows every file it will
    fetch, and the preflight sum stays a lower bound rather than a
    guess.

    Args:
        files: The selected repo files.

    Returns:
        ``(total_bytes, file_count)``.
    """
    total = sum(repo_file.size for repo_file in files if repo_file.size is not None)
    return total, len(files)


def human_size(n: int) -> str:
    """Render a byte count in binary units for prompts and errors.

    Byte-scale values render verbatim (``500 B``); anything larger uses
    one decimal in KiB/MiB/GiB/TiB/PiB.
    """
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in _BINARY_UNITS:
        value /= 1024
        if value < 1024 or unit == _BINARY_UNITS[-1]:
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable: the last unit always returns")


def already_staged_bytes(staging_dir: Path, to_download: Sequence[PlannedDownload]) -> int:
    """Sum the bytes of planned downloads already complete in staging.

    Staging survives an interrupted pull on the same volume the
    preflight measures, and the client reuses fully downloaded files
    there — counting them again would double-charge a resume. A staged
    file counts only when it exists at its staging path with exactly
    the hub-reported size; size-less files never count (no way to know
    they are complete).

    Args:
        staging_dir: The pull's staging directory.
        to_download: The downloads the plan calls for.

    Returns:
        Byte total of the already-staged, size-verified files.
    """
    total = 0
    for planned in to_download:
        expected = planned.repo_file.size
        if expected is None:
            continue
        staged = staging_dir / planned.repo_file.path
        if staged.is_file() and staged.stat().st_size == expected:
            total += expected
    return total


def require_disk_space(archive_root: Path, needed_bytes: int) -> None:
    """Refuse a pull whose bytes will not fit at the archive path.

    Args:
        archive_root: The archive the pull writes into.
        needed_bytes: Byte total of the files the pull must download.

    Raises:
        PullEnvError: If free space at ``archive_root`` is below
            ``needed_bytes``, stating required vs. available.
    """
    free = shutil.disk_usage(archive_root).free
    if needed_bytes > free:
        raise PullEnvError(
            f"not enough disk space at {archive_root}: this pull needs "
            f"{human_size(needed_bytes)} but only {human_size(free)} is available; "
            "free up space or point at a bigger volume"
        )
