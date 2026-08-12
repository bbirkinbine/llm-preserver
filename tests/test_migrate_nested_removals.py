"""Nested directory removal in a migration plan — spec 0017 criterion 17.

Live-use finding (2026-08-11): the real archive's GGUF trees nest docs
under ``gguf/docs/<publisher>--<repo>/``, and the plan listed the
parent ``gguf`` *before* that child while never listing the
intermediate ``gguf/docs`` at all. ``os.rmdir`` cannot remove a
directory that still contains one, so the run left empty shells behind
and quietly failed a removal the preview had promised.

A plan that promises what it cannot keep is the specific failure
``_emptied_dirs`` was written to avoid, so the property is pinned here:
every directory the move drains is listed, and children come first.
"""

from pathlib import Path

from migrate_shapes import build_directory, init_archive_dir

from llm_preserver.migrate import plan_migration

FOREIGN = "unsloth/tiny-chat-GGUF"
WEIGHT_REL = "gguf/tiny-chat-Q4_K_M.gguf"
DOC_REL = "gguf/docs/unsloth--tiny-chat-GGUF/README.md"
WEIGHT = b"q4 weight bytes"
DOC = b"# readme\n"


def nested_archive(root: Path) -> Path:
    """A directory whose foreign artifact nests docs two levels deep.

    The real archive's shape: ``gguf/*.gguf`` beside
    ``gguf/docs/<publisher>--<repo>/README.md``.
    """
    return build_directory(
        root,
        "Qwen/tiny-chat",
        [("gguf", FOREIGN, {WEIGHT_REL: WEIGHT, DOC_REL: DOC})],
    )


def test_every_drained_directory_is_listed(tmp_path: Path) -> None:
    root = init_archive_dir(tmp_path)
    model_dir = nested_archive(root)

    removals = plan_migration(root).units[0].removed_dirs

    # The intermediate is the one that was missing: without it, the
    # parent can never be removed.
    assert model_dir / "gguf" / "docs" in removals
    assert model_dir / "gguf" / "docs" / "unsloth--tiny-chat-GGUF" in removals
    assert model_dir / "gguf" in removals


def test_children_are_listed_before_their_parents(tmp_path: Path) -> None:
    root = init_archive_dir(tmp_path)
    model_dir = nested_archive(root)

    removals = plan_migration(root).units[0].removed_dirs

    for index, directory in enumerate(removals):
        for later in removals[index + 1 :]:
            assert directory not in later.parents, (
                f"{later} is inside {directory}, which is removed first"
            )
    assert model_dir in removals  # the whole directory drains


def test_the_run_actually_leaves_nothing_behind(tmp_path: Path) -> None:
    from llm_preserver.migrate import execute_migration

    root = init_archive_dir(tmp_path)
    nested_archive(root)

    execute_migration(root, plan_migration(root))

    assert not (root / "models" / "Qwen").exists()
    assert (root / "models" / "unsloth" / "tiny-chat-GGUF" / DOC_REL).is_file()
