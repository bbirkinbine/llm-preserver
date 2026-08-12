"""Lineage harvested by migration — spec 0017 pass 2, criterion 14.

The old nested layout encoded a curator's confirmed judgment: files
filed under ``Qwen/X`` were a conversion *of* ``Qwen/X``. That judgment
exists nowhere else once the path is gone, so migration lifts it into
the record — ``base_model`` with ``base_model_source: "migrated"``, the
third of the three attributions record schema v3 distinguishes (card,
asserted, migrated).

The tool still invents nothing (spec 0000's no-tool-judgment stance):
a directory that keeps its own files gains no lineage claim, because no
human ever made one about it.

Expected red (test-first): ``llm_preserver.migrate`` does not exist.
"""

import importlib
from pathlib import Path
from typing import Any

import pytest
from migrate_shapes import (
    RENAME_DIR_ID,
    RENAME_TARGET_ID,
    SPLIT_DIR_ID,
    SPLIT_TARGET_ID,
    init_archive_dir,
    pure_rename_archive,
    split_archive,
)

from llm_preserver.records import load_record


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


def test_files_moving_out_of_a_directory_record_it_as_their_base_model(
    archive_root: Path,
) -> None:
    split_archive(archive_root)

    run_migration(archive_root)

    assert load_record(model_dir(archive_root, SPLIT_TARGET_ID)).base_model == SPLIT_DIR_ID


def test_harvested_lineage_is_attributed_to_the_migration(archive_root: Path) -> None:
    """Three origins with different trustworthiness; a record that
    flattened them into one field could not be audited later."""
    split_archive(archive_root)

    run_migration(archive_root)

    record = load_record(model_dir(archive_root, SPLIT_TARGET_ID))
    assert record.base_model_source == "migrated"


def test_lineage_is_recorded_even_when_the_archive_does_not_hold_the_base(
    archive_root: Path,
) -> None:
    """The pure-rename case, and the only surviving trace of it: the
    archive holds unsloth's conversion and never held ``Qwen/tiny-chat``
    at all, so after the rename nothing but this field says what the
    directory name used to claim."""
    pure_rename_archive(archive_root)

    run_migration(archive_root)

    record = load_record(model_dir(archive_root, RENAME_TARGET_ID))
    assert record.base_model == RENAME_DIR_ID
    assert record.base_model_source == "migrated"
    assert not model_dir(archive_root, RENAME_DIR_ID).exists()  # base is not archived


def test_a_directory_that_kept_its_own_files_gains_no_lineage_claim(
    archive_root: Path,
) -> None:
    """No *tool-invented* lineage (spec 0017 non-goals): nobody ever
    asserted a base for the directory the quant moved out of."""
    source = split_archive(archive_root)

    run_migration(archive_root)

    record = load_record(source)
    assert record.base_model is None
    assert record.base_model_source is None


def test_every_migrated_directory_gets_the_generated_markdown_rendering(
    archive_root: Path,
) -> None:
    """A new model directory is a complete one: record, rendering, and
    manifest (ADR 0001). *What* the prose says about lineage is pass 5's
    rendering work; that it exists is this pass's.
    """
    split_archive(archive_root)

    run_migration(archive_root)

    assert (model_dir(archive_root, SPLIT_TARGET_ID) / "MODEL-RECORD.md").is_file()
