"""Moving locked payload — spec 0017, found in live use 2026-08-11.

ADR 0001 locks payload after download (``chmod a-w``). On an SMB share
that lock comes back as the BSD **user-immutable flag** (`uchg`): the
client stores the mode as the DOS read-only attribute and surfaces it
as ``UF_IMMUTABLE``. ``rename(2)`` on an immutable file fails with
EPERM, so migration could not move a single archived file on the real
NAS — the failure that stopped Brian's first rehearsal after a
ten-minute copy.

The fix mirrors what ``pull_transfer`` already does for the write bit:
unlock, move, re-lock. What these tests pin is that the *lock survives*
— a migration that silently left the archive's payload writable would
trade one bug for a quieter one.
"""

import os
import stat
from pathlib import Path

import pytest
from migrate_shapes import (
    Q4_REL,
    RENAME_DIR_ID,
    RENAME_TARGET_ID,
    init_archive_dir,
    pure_rename_archive,
)

from llm_preserver.migrate import execute_migration, plan_migration

pytestmark = pytest.mark.skipif(
    not hasattr(os, "chflags"), reason="BSD file flags are macOS/BSD only"
)


TOOL_OWNED = {"model-record.json", "MODEL-RECORD.md", "manifest-sha256.txt"}


def payload_paths(model_dir: Path) -> list[Path]:
    """Payload only — ADR 0001 locks weights, never the metadata.

    Verified against the real archive (2026-08-11): its `.gguf` files
    carry `uchg` and its record/markdown/manifest carry no flags at all.
    """
    return [
        p
        for p in model_dir.rglob("*")
        if p.is_file() and not (p.parent == model_dir and p.name in TOOL_OWNED)
    ]


def lock(path: Path) -> None:
    """Apply the immutable flag the way an SMB archive presents it."""
    os.chflags(path, path.stat().st_flags | stat.UF_IMMUTABLE)


def unlock_tree(root: Path) -> None:
    """Clear flags so pytest's tmp_path cleanup can remove the tree."""
    for path in root.rglob("*"):
        if path.is_file():
            os.chflags(path, path.stat().st_flags & ~stat.UF_IMMUTABLE)


@pytest.fixture
def immutable_archive(tmp_path: Path):
    root = init_archive_dir(tmp_path)
    model_dir = pure_rename_archive(root)
    for path in payload_paths(model_dir):
        lock(path)
    yield root
    unlock_tree(root)


def test_migration_moves_payload_that_carries_the_immutable_flag(
    immutable_archive: Path,
) -> None:
    # The live failure, reduced: EPERM on rename, ten minutes in.
    execute_migration(immutable_archive, plan_migration(immutable_archive))

    moved = immutable_archive / "models" / RENAME_TARGET_ID / Q4_REL
    assert moved.is_file()
    assert not (immutable_archive / "models" / RENAME_DIR_ID).exists()


def test_the_moved_payload_is_still_locked_afterwards(immutable_archive: Path) -> None:
    # The lock is ADR 0001's immutable-payload rule. Migration may
    # borrow it for the length of a rename; it may not spend it.
    execute_migration(immutable_archive, plan_migration(immutable_archive))

    moved = immutable_archive / "models" / RENAME_TARGET_ID / Q4_REL
    assert moved.stat().st_flags & stat.UF_IMMUTABLE


def test_an_unlocked_file_stays_unlocked(tmp_path: Path) -> None:
    # Restore what was there, not a blanket lock: a file the archive
    # never locked must not come out of a migration locked.
    root = init_archive_dir(tmp_path)
    pure_rename_archive(root)

    execute_migration(root, plan_migration(root))

    moved = root / "models" / RENAME_TARGET_ID / Q4_REL
    assert not moved.stat().st_flags & stat.UF_IMMUTABLE
