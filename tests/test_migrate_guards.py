"""Refusals that stop a migration — spec 0017 pass 2, sketch step 3.

``migrate`` moves irreplaceable bytes inside the archive, so every
condition it cannot resolve honestly stops the *whole* run before
anything moves: a target that already holds different bytes, a record
it cannot read, an artifact that claims no source, and the symlink
posture specs 0009 and 0010 established (this walk follows recorded
paths, and archives get copied from elsewhere).

Two more guards are about deletion, the one thing this command does
that ``CLAUDE.md`` reserves to ``remove``: emptied directories go via
``os.rmdir``, which fails rather than recursing, and ``rmtree`` is
never called at all.

Symlink refusals live in test_migrate_symlink_guards.py.

Expected red (test-first): ``llm_preserver.migrate`` does not exist.
"""

import errno
import importlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from migrate_shapes import (
    Q4,
    Q4_REL,
    Q8,
    Q8_REL,
    RENAME_TARGET_ID,
    SPLIT_DIR_ID,
    SPLIT_TARGET_ID,
    build_directory,
    init_archive_dir,
    pure_rename_archive,
    split_archive,
    tree_snapshot,
)

from llm_preserver.records import RECORD_FILENAME


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


def test_a_target_holding_different_bytes_refuses_the_whole_run(archive_root: Path) -> None:
    """Refuse-and-report is the safe default (open question 4): merging
    two publishers' claims on one path is a decision no measurement here
    supports."""
    source = split_archive(archive_root)
    squatter = model_dir(archive_root, SPLIT_TARGET_ID) / Q4_REL
    squatter.parent.mkdir(parents=True)
    squatter.write_bytes(b"different bytes entirely, and a different length")
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match=Q4_REL):
        plan_then_execute(archive_root)

    assert (source / Q4_REL).read_bytes() == Q4  # nothing moved
    assert squatter.read_bytes().startswith(b"different bytes")


def test_a_collision_stops_the_run_before_an_unrelated_directory_moves(
    archive_root: Path,
) -> None:
    """ "Refuse the whole run" — a partial migration around a blocked
    directory would leave the human guessing which half ran."""
    pure_rename_archive(archive_root)
    split_archive(archive_root)
    squatter = model_dir(archive_root, SPLIT_TARGET_ID) / Q4_REL
    squatter.parent.mkdir(parents=True)
    squatter.write_bytes(b"different bytes entirely, and a different length")
    before = tree_snapshot(archive_root)
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError):
        plan_then_execute(archive_root)

    assert tree_snapshot(archive_root) == before  # the rename did not run either


def test_an_unreadable_record_refuses_the_run_naming_the_directory(
    archive_root: Path,
) -> None:
    """A record that will not parse cannot be reasoned about, and the
    plan is entirely record-derived — guessing from the filesystem is
    exactly the inference ADR 0003 removes."""
    pure_rename_archive(archive_root)
    broken = build_directory(archive_root, "beta/coder", [("gguf", "beta/coder", {Q4_REL: Q4})])
    (broken / RECORD_FILENAME).write_text("{ not json at all", encoding="utf-8")
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match="beta/coder"):
        migrate.plan_migration(archive_root)


def test_an_artifact_with_no_source_repo_refuses_and_names_the_directory(
    archive_root: Path,
) -> None:
    """Open question 3's default: a null defeats the comparison the plan
    is derived from, so refuse rather than assume the files belong where
    they happen to sit."""
    build_directory(archive_root, SPLIT_DIR_ID, [("gguf", None, {Q4_REL: Q4})])
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match=SPLIT_DIR_ID):
        migrate.plan_migration(archive_root)


def test_a_record_naming_an_unusable_source_repo_refuses(archive_root: Path) -> None:
    """A ``source_repo`` this tool cannot read is one it cannot confirm —
    the same conviction ``layout.layout_state`` reaches, and here it also
    means no target path can be derived from it."""
    model = build_directory(archive_root, SPLIT_DIR_ID, [("gguf", SPLIT_TARGET_ID, {Q4_REL: Q4})])
    record = json.loads((model / RECORD_FILENAME).read_text(encoding="utf-8"))
    record["artifacts"][0]["source_repo"] = "https://example.invalid/../../etc"
    (model / RECORD_FILENAME).write_text(json.dumps(record), encoding="utf-8")
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match=SPLIT_DIR_ID):
        migrate.plan_migration(archive_root)


def test_a_stray_file_keeps_its_directory_and_is_never_swept(archive_root: Path) -> None:
    """Criterion 17: ``os.rmdir`` fails rather than recursing if anything
    at all remains, including a dotfile — a wrong plan cannot destroy
    content. The preview must not promise the removal either."""
    source = pure_rename_archive(archive_root)
    stray = source / ".DS_Store"
    stray.write_bytes(b"not ours, not payload, not deletable")

    unit = migrate_module().plan_migration(archive_root).units[0]
    assert source not in unit.removed_dirs  # not promised in the preview

    plan_then_execute(archive_root)

    assert stray.read_bytes() == b"not ours, not payload, not deletable"
    assert (model_dir(archive_root, RENAME_TARGET_ID) / Q4_REL).read_bytes() == Q4


def test_migration_never_calls_rmtree(archive_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The narrow, spec-sanctioned deletion is ``os.rmdir`` and nothing
    else: a recursive delete inside an archive is ``remove``'s job alone
    (``CLAUDE.md`` don't-touch list)."""
    pure_rename_archive(archive_root)
    split_archive(archive_root)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("migrate must never recurse a delete; os.rmdir only")

    monkeypatch.setattr(shutil, "rmtree", forbidden)

    plan_then_execute(archive_root)

    assert (model_dir(archive_root, RENAME_TARGET_ID) / Q8_REL).read_bytes() == Q8


def test_a_cross_filesystem_move_errors_naming_to_instead_of_copying(
    archive_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 12: an in-place move must be a rename or it must say so.
    A silent copy-and-delete doubles the space and moves the failure
    window onto irreplaceable bytes, so EXDEV surfaces as an error that
    names ``--to`` — the mode that copies on purpose."""
    source = pure_rename_archive(archive_root)

    def refuse_across_devices(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", refuse_across_devices)
    migrate = migrate_module()

    with pytest.raises(migrate.MigrateError, match="--to"):
        plan_then_execute(archive_root)

    monkeypatch.undo()
    assert (source / Q4_REL).read_bytes() == Q4  # still at the source, uncopied
    assert not (model_dir(archive_root, RENAME_TARGET_ID) / Q4_REL).exists()
