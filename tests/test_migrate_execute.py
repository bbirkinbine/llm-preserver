"""In-place execution of a migration — spec 0017 pass 2, criteria 10/15.

What ``migrate`` does to the tree: it moves exactly the files each
foreign artifact lists, writes the target record, rewrites the source
record without the moved artifact, regenerates both manifests, and
``os.rmdir``s what it emptied. No payload byte is re-downloaded,
re-hashed, or rewritten — so the proof that it worked is that ``verify``
reports the same hashes it recorded before the move.

Lineage harvesting lives in test_migrate_lineage.py, idempotence and
resume in test_migrate_resume.py, refusals in test_migrate_guards.py.

Expected red (test-first): ``llm_preserver.migrate`` does not exist.
"""

import hashlib
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
    RENAME_TARGET_ID,
    SNAPSHOT,
    SNAPSHOT_REL,
    SPLIT_DIR_ID,
    SPLIT_TARGET_ID,
    init_archive_dir,
    pure_rename_archive,
    sha256_hex,
    split_archive,
    two_publisher_archive,
)

from llm_preserver.archive import inventory
from llm_preserver.records import MANIFEST_FILENAME, RECORD_FILENAME, load_record
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


def test_rename_moves_every_file_into_the_publishing_repos_directory(
    archive_root: Path,
) -> None:
    pure_rename_archive(archive_root)

    run_migration(archive_root)

    target = model_dir(archive_root, RENAME_TARGET_ID)
    assert (target / Q4_REL).read_bytes() == Q4
    assert (target / Q8_REL).read_bytes() == Q8


def test_rename_leaves_no_directory_behind_under_the_old_name(archive_root: Path) -> None:
    """Fact 2 of ADR 0003: a directory named for a model whose weights the
    archive does not hold is the failure this converts away."""
    pure_rename_archive(archive_root)

    run_migration(archive_root)

    assert not (archive_root / "models" / "Qwen" / "tiny-chat").exists()
    assert not (archive_root / "models" / "Qwen").exists()


def test_split_keeps_the_directorys_own_files_exactly_where_they_were(
    archive_root: Path,
) -> None:
    source = split_archive(archive_root)

    run_migration(archive_root)

    assert (source / SNAPSHOT_REL).read_bytes() == SNAPSHOT
    assert (source / CONFIG_REL).read_bytes() == CONFIG


def test_split_moves_the_foreign_quant_out_and_empties_its_format_dir(
    archive_root: Path,
) -> None:
    source = split_archive(archive_root)

    run_migration(archive_root)

    assert (model_dir(archive_root, SPLIT_TARGET_ID) / Q4_REL).read_bytes() == Q4
    assert not (source / Q4_REL).exists()
    assert not (source / "gguf").exists()  # drained, then rmdir-ed


def test_split_source_record_no_longer_lists_the_moved_artifact(archive_root: Path) -> None:
    source = split_archive(archive_root)

    run_migration(archive_root)

    record = load_record(source)
    assert [artifact.format for artifact in record.artifacts] == ["hf-snapshot"]
    assert all(entry.path != Q4_REL for a in record.artifacts for entry in a.files)


def test_target_record_names_the_publishing_repo_as_its_hub_id(archive_root: Path) -> None:
    """ADR 0003's checkable invariant: path == hub_id == source_repo."""
    split_archive(archive_root)

    run_migration(archive_root)

    record = load_record(model_dir(archive_root, SPLIT_TARGET_ID))
    assert record.hub_id == SPLIT_TARGET_ID
    assert [a.source_repo for a in record.artifacts] == [
        f"https://huggingface.co/{SPLIT_TARGET_ID}"
    ]


def test_moved_files_keep_their_recorded_paths_and_hashes(archive_root: Path) -> None:
    """No payload byte is re-hashed or rewritten (criterion 10): the
    digest recorded before the move is the digest recorded after it, at
    the same artifact-relative path."""
    split_archive(archive_root)

    run_migration(archive_root)

    record = load_record(model_dir(archive_root, SPLIT_TARGET_ID))
    entries = [entry for artifact in record.artifacts for entry in artifact.files]
    assert [entry.path for entry in entries] == [Q4_REL]
    assert entries[0].sha256 == sha256_hex(Q4)
    assert entries[0].size == len(Q4)


def test_two_publishers_in_one_gguf_dir_are_separated_file_by_file(
    archive_root: Path,
) -> None:
    """The subtree-move bug this criterion exists to prevent: moving
    ``gguf/`` wholesale would carry this directory's own quant away with
    the foreign one."""
    source = two_publisher_archive(archive_root)

    run_migration(archive_root)

    assert (source / Q4_REL).read_bytes() == Q4  # the directory's own quant stayed
    assert (model_dir(archive_root, SPLIT_TARGET_ID) / Q8_REL).read_bytes() == Q8
    assert not (source / Q8_REL).exists()


def test_a_directory_that_kept_files_keeps_its_own_record_and_id(archive_root: Path) -> None:
    source = two_publisher_archive(archive_root)

    run_migration(archive_root)

    record = load_record(source)
    assert record.hub_id == SPLIT_DIR_ID
    assert [entry.path for a in record.artifacts for entry in a.files] == [Q4_REL]


def test_verify_reports_every_model_valid_and_ok_after_migration(archive_root: Path) -> None:
    """Criterion 15, the whole point: a converted archive verifies clean
    with no re-download, on both fixity and layout axes."""
    pure_rename_archive(archive_root)
    split_archive(archive_root)

    run_migration(archive_root)

    report = verify_archive(archive_root)
    assert {result.model_id: result.state for result in report.models} == {
        RENAME_TARGET_ID: "valid",
        SPLIT_DIR_ID: "valid",
        SPLIT_TARGET_ID: "valid",
    }
    assert {result.layout for result in report.models} == {"ok"}
    assert report.drifted is False


def test_status_reports_the_same_total_bytes_after_migration(archive_root: Path) -> None:
    """Criterion 15's other half: nothing is lost or double-counted — the
    bytes only changed directory."""
    pure_rename_archive(archive_root)
    split_archive(archive_root)
    before = sum(summary.total_size for summary in inventory(archive_root))

    run_migration(archive_root)

    assert sum(summary.total_size for summary in inventory(archive_root)) == before


def test_target_manifest_covers_the_payload_and_the_on_disk_record(
    archive_root: Path,
) -> None:
    """``manifest-sha256.txt`` is regenerated from the record, and its
    record line hashes the bytes actually on disk — a re-serialization is
    not byte-identical, and ``sha256sum -c`` would reject it forever
    (spec 0009's hard-won fact)."""
    split_archive(archive_root)

    run_migration(archive_root)

    target = model_dir(archive_root, SPLIT_TARGET_ID)
    lines = (target / MANIFEST_FILENAME).read_text(encoding="utf-8").splitlines()
    by_path = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines if line}
    assert by_path[Q4_REL] == sha256_hex(Q4)
    assert (
        by_path[RECORD_FILENAME]
        == hashlib.sha256((target / RECORD_FILENAME).read_bytes()).hexdigest()
    )


def test_source_manifest_drops_the_lines_for_files_that_left(archive_root: Path) -> None:
    source = split_archive(archive_root)

    run_migration(archive_root)

    manifest = (source / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert SNAPSHOT_REL in manifest
    assert Q4_REL not in manifest
