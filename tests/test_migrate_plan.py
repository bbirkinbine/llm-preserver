"""Plan derivation for ``migrate`` — spec 0017 pass 2, criteria 9/10/13.

The plan is *derived*, never stored (criterion 13: "there is no journal
file to lose"): a walk of ``models/*/*/model-record.json`` comparing
each artifact's ``source_repo`` to the record's own ``hub_id``. These
tests pin what that walk concludes on the two shapes the live archive
holds, plus the shape that decides the moves are file-level.

Nothing here moves a byte — planning is read-only, and one test asserts
exactly that, since ``--plan`` is the rehearsal the human runs on 683
GiB before committing to anything.

Expected red (test-first): ``llm_preserver.migrate`` does not exist, so
each test fails at the late import in its body (ModuleNotFoundError).

**Assumed surface**, mirroring ``remove``'s plan/execute/models split
(this suite defines it; the implementer follows):

    plan_migration(root) -> MigratePlan
    MigratePlan.units       list[DirectoryMigration], source-id sorted
    MigratePlan.total_size  bytes that will move
    DirectoryMigration.model_id / .model_dir / .kind ("rename"|"split")
    DirectoryMigration.moves        list[ArtifactMove]
    DirectoryMigration.removed_dirs dirs the run will os.rmdir
    ArtifactMove.repo_id / .target_dir / .files / .total_size
    MigrateError (archive fault), MigrateUserError (user input)
"""

import importlib
from pathlib import Path
from typing import Any

import pytest
from migrate_shapes import (
    CONFIG,
    CONFIG_REL,
    Q4,
    Q4_REL,
    Q8,
    Q8_REL,
    RENAME_DIR_ID,
    RENAME_TARGET_ID,
    SNAPSHOT,
    SNAPSHOT_REL,
    SPLIT_DIR_ID,
    SPLIT_TARGET_ID,
    build_directory,
    init_archive_dir,
    pure_rename_archive,
    split_archive,
    tree_snapshot,
    two_publisher_archive,
)


def migrate_module() -> Any:  # Any: the module does not exist yet (test-first)
    """Late import of the module under test (expected red: not yet written)."""
    return importlib.import_module("llm_preserver.migrate")


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    """An initialized (empty) archive under tmp_path."""
    return init_archive_dir(tmp_path)


def one_unit(archive_root: Path) -> Any:
    """Plan the archive and return its single directory unit."""
    plan = migrate_module().plan_migration(archive_root)
    assert len(plan.units) == 1
    return plan.units[0]


def test_directory_whose_artifacts_are_all_foreign_is_planned_as_a_rename(
    archive_root: Path,
) -> None:
    """One source repo owning every artifact means the whole directory
    is that repo's directory under a wrong name (criterion 9's "pure
    rename"), not a directory that has something to give away."""
    pure_rename_archive(archive_root)

    unit = one_unit(archive_root)

    assert unit.model_id == RENAME_DIR_ID
    assert unit.kind == "rename"


def test_rename_move_targets_the_source_repos_own_directory(archive_root: Path) -> None:
    pure_rename_archive(archive_root)

    unit = one_unit(archive_root)

    assert len(unit.moves) == 1
    move = unit.moves[0]
    assert move.repo_id == RENAME_TARGET_ID
    assert move.target_dir == archive_root / "models" / "unsloth" / "tiny-chat-GGUF"


def test_moved_files_keep_their_model_dir_relative_paths(archive_root: Path) -> None:
    """Format subdirectories stay (ADR 0003), so every path *inside* an
    artifact is unchanged by the move — which is why no hash has to be
    recomputed."""
    pure_rename_archive(archive_root)

    move = one_unit(archive_root).moves[0]

    assert sorted(move.files) == sorted([Q4_REL, Q8_REL])


def test_directory_with_its_own_snapshot_and_a_foreign_quant_is_a_split(
    archive_root: Path,
) -> None:
    split_archive(archive_root)

    unit = one_unit(archive_root)

    assert unit.model_id == SPLIT_DIR_ID
    assert unit.kind == "split"


def test_split_plans_only_the_foreign_artifacts_files_for_the_move(archive_root: Path) -> None:
    """The directory's own snapshot stays where it is: its ``source_repo``
    already agrees with the path, so it contradicts nothing."""
    split_archive(archive_root)

    unit = one_unit(archive_root)

    assert len(unit.moves) == 1
    move = unit.moves[0]
    assert move.repo_id == SPLIT_TARGET_ID
    assert move.files == [Q4_REL]
    assert SNAPSHOT_REL not in move.files
    assert CONFIG_REL not in move.files


def test_two_publishers_in_one_gguf_directory_move_file_by_file(archive_root: Path) -> None:
    """The correctness point of criterion 10: one ``gguf/`` directory can
    legally hold two publishers' files (``update_record`` keys artifacts
    by ``(format, source_repo)``), so a plan that named the *subtree*
    would relocate the directory's own bytes with the foreign ones."""
    two_publisher_archive(archive_root)

    unit = one_unit(archive_root)

    assert len(unit.moves) == 1
    move = unit.moves[0]
    assert move.repo_id == SPLIT_TARGET_ID
    assert move.files == [Q8_REL]  # the foreign quant only
    assert Q4_REL not in move.files  # this directory's own quant stays


def test_plan_totals_only_the_bytes_that_move(archive_root: Path) -> None:
    """Criterion 9's byte total sizes the transfer, so it must exclude
    the payload that stays put (544.5 GiB of foreign payload, not the
    whole archive)."""
    split_archive(archive_root)

    plan = migrate_module().plan_migration(archive_root)

    assert plan.units[0].moves[0].total_size == len(Q4)
    assert plan.total_size == len(Q4)
    assert plan.total_size < len(Q4) + len(SNAPSHOT) + len(CONFIG)


def test_rename_plans_to_remove_the_emptied_directory_and_its_owner(
    archive_root: Path,
) -> None:
    """Criterion 17: every removal is named in the preview *before* the
    confirm, so the plan has to carry them."""
    pure_rename_archive(archive_root)

    unit = one_unit(archive_root)

    assert archive_root / "models" / "Qwen" / "tiny-chat" in unit.removed_dirs
    assert archive_root / "models" / "Qwen" in unit.removed_dirs


def test_split_plans_to_remove_the_emptied_format_dir_but_not_the_model_dir(
    archive_root: Path,
) -> None:
    """A split keeps its own files, so only the drained ``gguf/`` goes."""
    split_archive(archive_root)

    unit = one_unit(archive_root)

    model_dir = archive_root / "models" / "acme" / "tiny-chat"
    assert model_dir / "gguf" in unit.removed_dirs
    assert model_dir not in unit.removed_dirs


def test_a_directory_holding_a_second_repo_of_its_owner_keeps_that_owner_dir(
    archive_root: Path,
) -> None:
    """``models/Qwen/`` must not be planned for removal while another
    Qwen repo still lives in it — ``os.rmdir`` would refuse anyway, but a
    preview promising a removal that cannot happen is a lie."""
    pure_rename_archive(archive_root)
    build_directory(archive_root, "Qwen/other-model", [("gguf", "Qwen/other-model", {Q4_REL: Q4})])

    unit = one_unit(archive_root)

    assert archive_root / "models" / "Qwen" / "tiny-chat" in unit.removed_dirs
    assert archive_root / "models" / "Qwen" not in unit.removed_dirs


def test_an_already_migrated_archive_plans_nothing(archive_root: Path) -> None:
    """Criterion 13's no-op half: every directory already agrees with its
    record, so there is nothing to derive."""
    build_directory(archive_root, SPLIT_TARGET_ID, [("gguf", SPLIT_TARGET_ID, {Q4_REL: Q4})])
    build_directory(
        archive_root, SPLIT_DIR_ID, [("hf-snapshot", SPLIT_DIR_ID, {SNAPSHOT_REL: SNAPSHOT})]
    )

    plan = migrate_module().plan_migration(archive_root)

    assert plan.units == []
    assert plan.total_size == 0


def test_planning_changes_nothing_on_disk(archive_root: Path) -> None:
    """``--plan`` is the rehearsal run on an irreplaceable archive; it may
    not so much as regenerate a manifest."""
    pure_rename_archive(archive_root)
    split_archive(archive_root)
    before = tree_snapshot(archive_root)

    migrate_module().plan_migration(archive_root)

    assert tree_snapshot(archive_root) == before


def test_every_planned_directory_is_reported_once(archive_root: Path) -> None:
    """A mixed archive plans both shapes in one pass — the live archive is
    3 renames and 8 splits, not one or the other."""
    pure_rename_archive(archive_root)
    split_archive(archive_root)

    plan = migrate_module().plan_migration(archive_root)

    assert {unit.model_id: unit.kind for unit in plan.units} == {
        RENAME_DIR_ID: "rename",
        SPLIT_DIR_ID: "split",
    }
    assert plan.total_size == len(Q4) + len(Q8) + len(Q4)
