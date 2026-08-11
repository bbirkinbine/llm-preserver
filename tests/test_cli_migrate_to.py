"""``migrate --to`` — spec 0017 pass 2, criterion 11.

The reversible mode, and the one the live check runs first: write a
converted archive at a *new* root and leave the source untouched and
still usable. ``--repo`` (repeatable) scopes the copy to named source
directories so a rehearsal costs minutes instead of a whole-archive
transfer — the result is a valid archive holding a subset, which ADR
0001 already supports ("rsync one directory and you have exactly one
repo's bytes plus a record that names that repo").

Byte copy only: hardlinking payload was considered and rejected, so
these tests assert on inode identity and link counts rather than on
content alone — identical bytes prove nothing about which mechanism
produced them.

Expected red (test-first): the `migrate` command is not registered.
"""

import json
import shutil
from pathlib import Path

import pytest
from migrate_shapes import (
    Q4,
    Q4_REL,
    Q8_REL,
    RENAME_DIR_ID,
    RENAME_TARGET_ID,
    SNAPSHOT_REL,
    SPLIT_DIR_ID,
    SPLIT_TARGET_ID,
    init_archive_dir,
    output_of,
    pure_rename_archive,
    split_archive,
    tree_snapshot,
)
from typer.testing import CliRunner

from llm_preserver.archive import MARKER_FILENAME, SCHEMA_VERSION, is_archive
from llm_preserver.cli import app
from llm_preserver.records import load_record

runner = CliRunner()

RENAME_TARGET_REL = "models/unsloth/tiny-chat-GGUF"


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """An initialized archive holding one pure rename and one split."""
    root = init_archive_dir(tmp_path)
    pure_rename_archive(root)
    split_archive(root)
    return root


def invoke_migrate(archive: Path, *extra: str):
    """Run ``migrate <archive> [extra...]`` through the CliRunner."""
    return runner.invoke(app, ["migrate", str(archive), *extra])


def test_to_leaves_the_source_archive_byte_for_byte_untouched(
    archive: Path, tmp_path: Path
) -> None:
    """The whole point of the reversible mode: if the copy is wrong, the
    original is still the original."""
    before = tree_snapshot(archive)

    result = invoke_migrate(archive, "--yes", "--to", str(tmp_path / "copy"))

    assert result.exit_code == 0
    assert tree_snapshot(archive) == before


def test_to_writes_a_usable_archive_at_the_new_root(archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest))

    assert result.exit_code == 0
    assert is_archive(dest)
    marker = json.loads((dest / MARKER_FILENAME).read_text(encoding="utf-8"))
    # Whatever layout version this tool writes — pass 3 bumps it to 2,
    # and a fresh archive must never claim a version pull cannot produce.
    assert marker["schema_version"] == SCHEMA_VERSION


def test_the_copy_carries_the_converted_layout(archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest))

    assert result.exit_code == 0
    assert (dest / RENAME_TARGET_REL / Q4_REL).read_bytes() == Q4
    assert not (dest / "models" / "Qwen").exists()  # the misleading name does not travel


def test_the_copy_carries_the_harvested_lineage(archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest))

    assert result.exit_code == 0
    record = load_record(dest / RENAME_TARGET_REL)
    assert record.base_model == RENAME_DIR_ID
    assert record.base_model_source == "migrated"


def test_an_unscoped_copy_carries_the_directories_that_needed_no_move(
    archive: Path, tmp_path: Path
) -> None:
    """A *converted archive*, not just the converted parts: a copy that
    silently dropped the models with nothing to migrate would be a
    surprise the human discovers years later."""
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest))

    assert result.exit_code == 0
    assert (dest / "models" / "acme" / "tiny-chat" / SNAPSHOT_REL).is_file()


def test_payload_is_copied_by_bytes_and_never_hardlinked(archive: Path, tmp_path: Path) -> None:
    """Two trees sharing inodes cuts against ADR 0001's immutable-payload
    separation, and network-share support for it is unverified here — so
    a hardlinked "copy" is a defect, not an optimization."""
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest))

    assert result.exit_code == 0
    source_file = archive / "models" / "Qwen" / "tiny-chat" / Q4_REL
    copied = dest / RENAME_TARGET_REL / Q4_REL
    assert copied.read_bytes() == source_file.read_bytes()
    assert copied.stat().st_ino != source_file.stat().st_ino
    assert source_file.stat().st_nlink == 1
    assert copied.stat().st_nlink == 1


def test_repo_scopes_the_copy_to_the_named_source_directory(archive: Path, tmp_path: Path) -> None:
    """The rehearsal shape: one pure rename, minutes instead of a whole
    archive."""
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest), "--repo", RENAME_DIR_ID)

    assert result.exit_code == 0
    assert (dest / RENAME_TARGET_REL / Q8_REL).is_file()
    assert not (dest / "models" / "acme").exists()
    assert not (dest / "models" / "other").exists()


def test_repo_is_repeatable(archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "copy"

    result = invoke_migrate(
        archive, "--yes", "--to", str(dest), "--repo", RENAME_DIR_ID, "--repo", SPLIT_DIR_ID
    )

    assert result.exit_code == 0
    assert (dest / RENAME_TARGET_REL / Q4_REL).is_file()
    assert (dest / "models" / "acme" / "tiny-chat" / SNAPSHOT_REL).is_file()
    assert (dest / "models" / "other" / "tiny-chat-GGUF" / Q4_REL).is_file()


def test_a_scoped_copy_carries_what_the_named_directory_splits_into(
    archive: Path, tmp_path: Path
) -> None:
    """ "Only those models and whatever they split into": naming the
    source directory has to bring the foreign quant's new home with it,
    or the rehearsal proves nothing about the split case."""
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest), "--repo", SPLIT_DIR_ID)

    assert result.exit_code == 0
    assert load_record(dest / "models" / "other" / "tiny-chat-GGUF").hub_id == SPLIT_TARGET_ID
    assert not (dest / "models" / "Qwen").exists()
    assert not (dest / RENAME_TARGET_REL).exists()


def test_repo_naming_no_directory_is_a_user_error(archive: Path, tmp_path: Path) -> None:
    """Exit 2 is the user-input domain (spec 0009): a typo'd rehearsal
    must not silently produce an empty archive the human then trusts."""
    dest = tmp_path / "copy"

    result = invoke_migrate(archive, "--yes", "--to", str(dest), "--repo", "acme/nope")

    assert result.exit_code == 2
    assert "acme/nope" in output_of(result)


def test_a_partial_destination_is_resumed_not_refused(archive: Path, tmp_path: Path) -> None:
    """Live failure, 2026-08-11: the copy of a 16 GiB model took ten
    minutes, the conversion inside it failed, and the retry died with an
    unhandled FileExistsError from copytree. A rehearsal you cannot
    re-run is not a rehearsal."""
    dest = tmp_path / "copy"
    invoke_migrate(archive, "--yes", "--to", str(dest), "--repo", RENAME_DIR_ID)
    # Put the destination back into the mid-run shape: payload copied,
    # conversion not yet applied.
    shutil.rmtree(dest / "models" / RENAME_TARGET_ID.split("/")[0])
    shutil.copytree(
        archive / "models" / RENAME_DIR_ID, dest / "models" / RENAME_DIR_ID, dirs_exist_ok=True
    )

    result = invoke_migrate(archive, "--yes", "--to", str(dest), "--repo", RENAME_DIR_ID)

    assert result.exit_code == 0, output_of(result)
    assert (dest / "models" / RENAME_TARGET_ID / Q4_REL).is_file()
