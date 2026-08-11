"""Owner-directory removal across a whole plan — review, 2026-08-11.

Emptiness is not a per-unit fact. When two model directories under one
owner are both pure renames, each unit saw a sibling still present and
neither claimed the parent, so ``models/<owner>/`` survived empty — a
removal the preview promised and the run did not make.
"""

from pathlib import Path

from migrate_shapes import Q4, Q4_REL, build_directory, init_archive_dir

from llm_preserver.migrate import execute_migration, plan_migration

FOREIGN_A = "unsloth/model-a-GGUF"
FOREIGN_B = "unsloth/model-b-GGUF"


def two_renames_under_one_owner(root: Path) -> None:
    """Two directories under ``Qwen/`` that both move away entirely."""
    build_directory(root, "Qwen/model-a", [("gguf", FOREIGN_A, {Q4_REL: Q4})])
    build_directory(root, "Qwen/model-b", [("gguf", FOREIGN_B, {Q4_REL: Q4})])


def test_the_shared_owner_directory_is_planned_for_removal(tmp_path: Path) -> None:
    root = init_archive_dir(tmp_path)
    two_renames_under_one_owner(root)

    removals = [d for unit in plan_migration(root).units for d in unit.removed_dirs]

    assert root / "models" / "Qwen" in removals


def test_the_shared_owner_directory_is_actually_removed(tmp_path: Path) -> None:
    root = init_archive_dir(tmp_path)
    two_renames_under_one_owner(root)

    execute_migration(root, plan_migration(root))

    assert not (root / "models" / "Qwen").exists()


def test_an_owner_keeping_a_model_survives(tmp_path: Path) -> None:
    # The guard on the fix: only an owner losing *everything* goes.
    root = init_archive_dir(tmp_path)
    build_directory(root, "Qwen/model-a", [("gguf", FOREIGN_A, {Q4_REL: Q4})])
    build_directory(root, "Qwen/model-kept", [("gguf", "Qwen/model-kept", {Q4_REL: Q4})])

    execute_migration(root, plan_migration(root))

    assert (root / "models" / "Qwen" / "model-kept").is_dir()
    assert (root / "models" / "Qwen").is_dir()
