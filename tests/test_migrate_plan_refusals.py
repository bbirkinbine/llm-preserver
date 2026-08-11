"""What the plan must refuse before anything moves — review, 2026-08-11.

Both reviewers converged on one diagnosis: the executor kept
discovering problems the *planner* could already see, after payload had
moved. `docs/cli.md` promises "the run refuses as a whole — never
half-converts", and these are the cases where it did not.

Each is reachable without hostility: a file an interrupted pull left
missing, a directory whose name differs from its recorded source only
in case (macOS APFS is case-insensitive by default), and two artifacts
recording the same path.
"""

import json
from pathlib import Path

import pytest
from migrate_shapes import Q4, Q4_REL, build_directory, init_archive_dir

from llm_preserver.migrate import MigrateError, plan_migration

FOREIGN = "unsloth/tiny-chat-GGUF"


def test_a_recorded_file_missing_from_disk_refuses_the_run(tmp_path: Path) -> None:
    # Exactly what spec 0012 says an interrupted pull produces, and what
    # verify already reports as `incomplete`. Previously: an unhandled
    # FileNotFoundError mid-run, payload orphaned, every re-run
    # tracebacking, pull and remove gated forever.
    root = init_archive_dir(tmp_path)
    model_dir = build_directory(root, "Qwen/tiny-chat", [("gguf", FOREIGN, {Q4_REL: Q4})])
    (model_dir / Q4_REL).unlink()

    with pytest.raises(MigrateError) as excinfo:
        plan_migration(root)

    assert "Qwen/tiny-chat" in str(excinfo.value)
    assert Q4_REL in str(excinfo.value)


def test_a_case_only_difference_refuses_rather_than_renaming_onto_itself(
    tmp_path: Path,
) -> None:
    # On a case-insensitive filesystem the target *is* the source, so
    # every move is a no-op and the source-record cleanup then deletes
    # the only copy of the hashes, license and provenance — while
    # exiting 0 reporting success.
    root = init_archive_dir(tmp_path)
    build_directory(root, "unsloth/Tiny-Chat-GGUF", [("gguf", FOREIGN, {Q4_REL: Q4})])

    with pytest.raises(MigrateError) as excinfo:
        plan_migration(root)

    assert "case" in str(excinfo.value).lower()


def test_two_artifacts_claiming_one_path_refuse_the_run(tmp_path: Path) -> None:
    # Whichever moves first wins and the second's move silently
    # vanishes, or worse, the file is recorded in two places.
    root = init_archive_dir(tmp_path)
    model_dir = build_directory(root, "Qwen/tiny-chat", [("gguf", FOREIGN, {Q4_REL: Q4})])
    record = json.loads((model_dir / "model-record.json").read_text())
    duplicate = json.loads(json.dumps(record["artifacts"][0]))
    duplicate["source_repo"] = "mradermacher/tiny-chat-GGUF"
    record["artifacts"].append(duplicate)
    (model_dir / "model-record.json").write_text(json.dumps(record, indent=2) + "\n")

    with pytest.raises(MigrateError) as excinfo:
        plan_migration(root)

    assert Q4_REL in str(excinfo.value)


def test_a_sound_archive_still_plans(tmp_path: Path) -> None:
    # The guard on all three: none of them may refuse a normal archive.
    root = init_archive_dir(tmp_path)
    build_directory(root, "Qwen/tiny-chat", [("gguf", FOREIGN, {Q4_REL: Q4})])

    assert len(plan_migration(root).units) == 1
