"""Idempotence and resume — spec 0017 pass 2, criterion 13.

683 GiB of renames is not instantaneous, so a half-migrated archive is
an expected state rather than a fault. There is no journal file: the
plan is re-derived from ``hub_id`` vs. ``source_repo`` vs. path, which
means the *disk* has to be the thing that says what is left to do.

The interrupted state is built here by hand rather than by killing a
run, so the test pins the property (a mid-move crash converges on the
next run) instead of one implementation's crash point: files already
moved, no record at the target yet, the source record still claiming
them.

Expected red (test-first): ``llm_preserver.migrate`` does not exist.
"""

import importlib
from pathlib import Path
from typing import Any

import pytest
from migrate_shapes import (
    Q4,
    Q4_REL,
    SNAPSHOT_REL,
    SPLIT_DIR_ID,
    SPLIT_TARGET_ID,
    init_archive_dir,
    split_archive,
    tree_snapshot,
)

from llm_preserver.records import RECORD_FILENAME, load_record
from llm_preserver.verify import verify_archive


def migrate_module() -> Any:  # Any: the module does not exist yet (test-first)
    """Late import of the module under test (expected red: not yet written)."""
    return importlib.import_module("llm_preserver.migrate")


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    """An initialized (empty) archive under tmp_path."""
    return init_archive_dir(tmp_path)


def run_migration(archive_root: Path) -> None:
    """Plan and execute an in-place migration of the whole archive."""
    migrate = migrate_module()
    migrate.execute_migration(archive_root, migrate.plan_migration(archive_root))


def model_dir(archive_root: Path, model_id: str) -> Path:
    """``models/<owner>/<repo>`` for a repo id."""
    owner, _, repo = model_id.partition("/")
    return archive_root / "models" / owner / repo


def interrupt_after_the_move(archive_root: Path) -> Path:
    """Reproduce a crash between the file move and the record write.

    The payload is already at its target, the target has no record yet,
    and the source record still lists the artifact — the state the
    spec's execution order deliberately produces, so that the next plan
    still sees the work.
    """
    source = split_archive(archive_root)
    target = model_dir(archive_root, SPLIT_TARGET_ID)
    (target / "gguf").mkdir(parents=True)
    (source / Q4_REL).replace(target / Q4_REL)
    return source


def test_re_running_on_a_migrated_archive_moves_nothing(archive_root: Path) -> None:
    split_archive(archive_root)
    run_migration(archive_root)
    after_first = tree_snapshot(archive_root)

    run_migration(archive_root)

    assert tree_snapshot(archive_root) == after_first


def test_the_second_run_finds_nothing_to_do(archive_root: Path) -> None:
    """Criterion 13's no-op half, stated as a plan rather than a diff."""
    split_archive(archive_root)
    run_migration(archive_root)

    assert migrate_module().plan_migration(archive_root).units == []


def test_a_half_migrated_archive_re_plans_the_unfinished_move(archive_root: Path) -> None:
    """The source record is the only durable statement of what was in
    flight, so the plan must still name the move after the crash."""
    interrupt_after_the_move(archive_root)

    plan = migrate_module().plan_migration(archive_root)

    assert len(plan.units) == 1
    assert plan.units[0].moves[0].repo_id == SPLIT_TARGET_ID
    assert plan.units[0].moves[0].files == [Q4_REL]


def test_a_file_already_at_its_target_is_not_a_collision(archive_root: Path) -> None:
    """Resumability outranks the collision refusal: identical bytes at
    the target are this migration's own interrupted work, not a
    conflicting directory (criterion 13 vs. the guard in the sketch)."""
    interrupt_after_the_move(archive_root)

    run_migration(archive_root)

    assert (model_dir(archive_root, SPLIT_TARGET_ID) / Q4_REL).read_bytes() == Q4


def test_the_resumed_run_writes_the_record_the_crash_skipped(archive_root: Path) -> None:
    interrupt_after_the_move(archive_root)

    run_migration(archive_root)

    record = load_record(model_dir(archive_root, SPLIT_TARGET_ID))
    assert record.hub_id == SPLIT_TARGET_ID
    assert [entry.path for a in record.artifacts for entry in a.files] == [Q4_REL]


def test_the_resumed_run_finishes_the_source_record_surgery(archive_root: Path) -> None:
    source = interrupt_after_the_move(archive_root)

    run_migration(archive_root)

    record = load_record(source)
    assert [artifact.format for artifact in record.artifacts] == ["hf-snapshot"]
    assert (source / RECORD_FILENAME).is_file()


def test_a_resumed_archive_verifies_clean_on_both_axes(archive_root: Path) -> None:
    interrupt_after_the_move(archive_root)

    run_migration(archive_root)

    report = verify_archive(archive_root)
    assert {result.model_id for result in report.models} == {SPLIT_DIR_ID, SPLIT_TARGET_ID}
    assert {result.state for result in report.models} == {"valid"}
    assert {result.layout for result in report.models} == {"ok"}


def test_the_stranded_empty_format_dir_is_cleared_by_the_resumed_run(
    archive_root: Path,
) -> None:
    """The crash left ``gguf/`` behind with nothing in it; a converged
    archive has no phantom directories."""
    source = interrupt_after_the_move(archive_root)
    assert (source / "gguf").is_dir()  # precondition: the crash's leftover

    run_migration(archive_root)

    assert not (source / "gguf").exists()
    assert (source / SNAPSHOT_REL).is_file()
