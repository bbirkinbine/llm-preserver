"""Symlink containment for ``migrate`` — spec 0017 pass 2, sketch step 3.

An archive may be copied from anywhere, so every recorded path is
untrusted input. ``migrate`` follows recorded paths *and* creates new
directories from recorded ``source_repo`` values, which makes it the
first command that could both read and write through a planted link.
The posture is the one specs 0009 and 0010 established: resolve, check
containment, refuse — and prove the outside data survived.

Each case is its own test because each is a different planting point:
the leaf file, an intermediate directory, and the owner directory the
walk itself would have to enter.

Expected red (test-first): ``llm_preserver.migrate`` does not exist.
"""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from migrate_shapes import (
    Q4,
    Q4_REL,
    RENAME_TARGET_ID,
    SPLIT_TARGET_ID,
    build_directory,
    init_archive_dir,
    pure_rename_archive,
    tree_snapshot,
)

from llm_preserver.records import RECORD_FILENAME

OUTSIDE_BYTES = b"precious data outside the archive"
REFUSAL = r"symlink|outside|escape"
"""Any wording that refuses; the test is about the refusal, not the phrase."""


def migrate_module() -> Any:  # Any: the module does not exist yet (test-first)
    """Late import of the module under test (expected red: not yet written)."""
    return importlib.import_module("llm_preserver.migrate")


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    """An initialized (empty) archive under tmp_path."""
    return init_archive_dir(tmp_path)


def plan_then_execute(archive_root: Path) -> None:
    """Plan and execute an in-place migration of the whole archive."""
    migrate = migrate_module()
    migrate.execute_migration(archive_root, migrate.plan_migration(archive_root))


def model_dir(archive_root: Path, model_id: str) -> Path:
    """``models/<owner>/<repo>`` for a repo id."""
    owner, _, repo = model_id.partition("/")
    return archive_root / "models" / owner / repo


def test_a_recorded_file_that_is_a_symlink_is_refused(archive_root: Path, tmp_path: Path) -> None:
    """Moving the link would hand its target's path to another repo's
    directory and record someone else's bytes as archived payload."""
    victim = tmp_path / "outside.gguf"
    victim.write_bytes(OUTSIDE_BYTES)
    source = pure_rename_archive(archive_root)
    (source / Q4_REL).unlink()
    (source / Q4_REL).symlink_to(victim)
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match=REFUSAL):
        plan_then_execute(archive_root)

    assert victim.read_bytes() == OUTSIDE_BYTES
    assert not (model_dir(archive_root, RENAME_TARGET_ID) / Q4_REL).exists()


def test_a_recorded_path_through_a_symlinked_directory_is_refused(
    archive_root: Path, tmp_path: Path
) -> None:
    """The leaf-only check misses ``<model>/<symlinked-format-dir>/file``
    — the exact vector spec 0010's review round PoC-confirmed."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "tiny-chat-Q4_K_M.gguf"
    victim.write_bytes(OUTSIDE_BYTES)
    source = build_directory(archive_root, "Qwen/tiny-chat", [])
    record = json.loads((source / RECORD_FILENAME).read_text(encoding="utf-8"))
    record["artifacts"] = [
        {
            "format": "gguf",
            "quantization": None,
            "source_repo": f"https://huggingface.co/{RENAME_TARGET_ID}",
            "revision": "a" * 40,
            "download_date": "2026-07-09",
            "runtime_tested": None,
            "provenance": "hashed-locally",
            "files": [{"path": Q4_REL, "sha256": None, "size": None, "source": "original"}],
        }
    ]
    (source / RECORD_FILENAME).write_text(json.dumps(record), encoding="utf-8")
    (source / "gguf").symlink_to(outside, target_is_directory=True)
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match=REFUSAL):
        plan_then_execute(archive_root)

    assert victim.read_bytes() == OUTSIDE_BYTES


def test_a_symlinked_owner_directory_is_never_migrated_through(
    archive_root: Path, tmp_path: Path
) -> None:
    """``models/<owner>`` itself can be the link on a copied archive.
    The shared walk refuses to follow it (``iter_model_dirs``), so the
    tree behind it is neither planned nor touched."""
    outside = tmp_path / "outside"
    (outside / "tiny-chat" / "gguf").mkdir(parents=True)
    victim = outside / "tiny-chat" / "gguf" / "tiny-chat-Q4_K_M.gguf"
    victim.write_bytes(OUTSIDE_BYTES)
    (archive_root / "models" / "Qwen").symlink_to(outside, target_is_directory=True)
    before = tree_snapshot(outside)

    plan_then_execute(archive_root)

    assert tree_snapshot(outside) == before
    assert victim.read_bytes() == OUTSIDE_BYTES


def test_a_symlinked_target_directory_is_refused_not_written_through(
    archive_root: Path, tmp_path: Path
) -> None:
    """The destination is derived from an untrusted ``source_repo``, so a
    planted link at the target would let a migration write payload and a
    record outside the archive root."""
    outside = tmp_path / "outside"
    outside.mkdir()
    build_directory(archive_root, "acme/tiny-chat", [("gguf", SPLIT_TARGET_ID, {Q4_REL: Q4})])
    (archive_root / "models" / "other").symlink_to(outside, target_is_directory=True)
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match=REFUSAL):
        plan_then_execute(archive_root)

    assert list(outside.iterdir()) == []  # nothing written outside the archive
