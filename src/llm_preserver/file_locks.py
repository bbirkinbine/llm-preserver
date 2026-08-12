"""ADR 0001's payload lock, and getting past it deliberately.

Payload is locked after download (``chmod a-w``). On an SMB share that
lock comes back as the BSD **user-immutable flag** (`uchg`): the client
stores the mode as the DOS read-only attribute and macOS surfaces it as
``UF_IMMUTABLE``. Both ``rename(2)`` and ``unlink(2)`` fail with EPERM
on an immutable file, so every sanctioned operation that relocates or
deletes payload has to clear the flag first — `migrate` when it moves a
file, `remove` when it deletes one.

The rule these helpers exist to keep: the lock is **borrowed, never
spent**. A move restores it on the moved file; a delete does not need
to restore anything, because the file is gone. Nothing here ever leaves
a surviving file less locked than it found it.

Found in live use on a real NAS (2026-08-11), where migration could not
move a single archived file and `remove` would have deleted a model's
record and then failed on its first weight.
"""

import os
import stat
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

_IMMUTABLE = stat.UF_IMMUTABLE | getattr(stat, "SF_IMMUTABLE", 0)

# Resolved through getattr, not referenced directly: BSD file flags do
# not exist on Linux, and mypy type-checks against the platform it runs
# on — so a bare ``os.chflags`` passes locally on macOS and fails CI.
# Every caller degrades to a no-op where the mechanism is absent.
_chflags: Callable[[Path, int], None] | None = getattr(os, "chflags", None)


def _current_flags(path: Path) -> int:
    """The file's BSD flags, or 0 on a platform that has none."""
    return int(getattr(path.stat(), "st_flags", 0))


def immutable_flags(path: Path) -> int:
    """The file's immutable bits, or 0 where the platform has no flags.

    Linux has no ``st_flags``; the whole mechanism is a BSD/macOS one,
    so this is 0 there and every caller becomes a no-op.
    """
    if _chflags is None:
        return 0
    return _current_flags(path) & _IMMUTABLE


def clear_immutable(path: Path) -> int:
    """Clear the immutable bits, returning what was cleared.

    Returns:
        The bits removed, for handing back to :func:`restore_immutable`.
        0 when there was nothing to clear.

    Raises:
        OSError: If the flag cannot be cleared — the caller decides
            whether that is fatal, since it means this user cannot
            modify the archive's payload at all.
    """
    locked = immutable_flags(path)
    if locked and _chflags is not None:
        _chflags(path, _current_flags(path) & ~locked)
    return locked


def restore_immutable(path: Path, locked: int) -> None:
    """Put back bits cleared earlier, best effort.

    Best effort on purpose: this runs on the *success* path after a
    move, and on the rollback path after a failure. Neither is a place
    to raise a second exception over a flag.
    """
    if not locked or _chflags is None:
        return
    with suppress(OSError):
        _chflags(path, _current_flags(path) | locked)


def unlink_locked(path: Path) -> None:
    """Delete a file even when the archive's payload lock is on it.

    The lock protects payload from accident, not from the one sanctioned
    deletion path (``remove``, spec 0010). Unlinking a symlink never
    touches its target, so clearing flags here cannot reach outside the
    archive.

    Raises:
        OSError: If the file cannot be unlinked after unlocking.
    """
    locked = 0
    if not path.is_symlink():
        with suppress(OSError):
            locked = clear_immutable(path)
    try:
        path.unlink()
    except OSError:
        # The file survives, so it must survive as locked as it was —
        # a half-failed `remove` that leaves the remaining weights
        # writable spends a lock it only borrowed (review, 2026-08-11).
        restore_immutable(path, locked)
        raise
