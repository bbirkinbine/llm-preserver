"""The archive marker flips only after a full migration (criterion 24).

Review finding, 2026-08-11: nothing in ``migrate`` ever touched
``archive.json``. The criterion calls the flip "the single durable
signal that migration finished" and ADR 0003 requires it so an older
tool refuses a converted archive — neither held, and the author's real
archive was converted while still declaring v1.

The scoped cases are the point: a `--repo` run converts part of an
archive, so it must *not* claim the whole thing is done.
"""

import json
from pathlib import Path

from migrate_shapes import Q4, Q4_REL, build_directory, init_archive_dir

from llm_preserver.archive import MARKER_FILENAME, SCHEMA_VERSION
from llm_preserver.migrate import execute_migration, plan_migration

FOREIGN_A = "unsloth/model-a-GGUF"
FOREIGN_B = "unsloth/model-b-GGUF"


def marker_version(root: Path) -> int:
    return int(json.loads((root / MARKER_FILENAME).read_text(encoding="utf-8"))["schema_version"])


def v1_archive(tmp_path: Path) -> Path:
    root = init_archive_dir(tmp_path)
    (root / MARKER_FILENAME).write_text(
        json.dumps({"tool": "llm-preserver", "schema_version": 1}), encoding="utf-8"
    )
    return root


def test_a_complete_migration_flips_the_marker(tmp_path: Path) -> None:
    root = v1_archive(tmp_path)
    build_directory(root, "Qwen/model-a", [("gguf", FOREIGN_A, {Q4_REL: Q4})])

    execute_migration(root, plan_migration(root))

    assert marker_version(root) == SCHEMA_VERSION


def test_a_scoped_migration_leaves_the_marker_alone(tmp_path: Path) -> None:
    # Half an archive converted must not claim the whole one is.
    root = v1_archive(tmp_path)
    build_directory(root, "Qwen/model-a", [("gguf", FOREIGN_A, {Q4_REL: Q4})])
    build_directory(root, "Qwen/model-b", [("gguf", FOREIGN_B, {Q4_REL: Q4})])

    execute_migration(root, plan_migration(root, ["Qwen/model-a"]))

    assert marker_version(root) == 1


def test_finishing_the_rest_then_flips_it(tmp_path: Path) -> None:
    root = v1_archive(tmp_path)
    build_directory(root, "Qwen/model-a", [("gguf", FOREIGN_A, {Q4_REL: Q4})])
    build_directory(root, "Qwen/model-b", [("gguf", FOREIGN_B, {Q4_REL: Q4})])
    execute_migration(root, plan_migration(root, ["Qwen/model-a"]))

    execute_migration(root, plan_migration(root))

    assert marker_version(root) == SCHEMA_VERSION


def test_an_already_converted_archive_is_flipped_by_a_no_op_run(tmp_path: Path) -> None:
    # A v1 archive that already conforms still needs the signal.
    root = v1_archive(tmp_path)
    build_directory(root, FOREIGN_A, [("gguf", FOREIGN_A, {Q4_REL: Q4})])

    execute_migration(root, plan_migration(root))

    assert marker_version(root) == SCHEMA_VERSION
