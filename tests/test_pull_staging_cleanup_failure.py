"""Spec 0019: a failed staging cleanup must not fail the pull.

Today ``shutil.rmtree(prep.staging_dir)`` sits inside the same ``try``
whose ``except OSError`` raises ``PullEnvError`` (fault domain "local
environment", exit 3), and it runs *after* ``write_manifest`` and
``save_record`` — so a pull whose bytes, hashes, record, and manifest
all landed is reported as a failure when deleting hf's bookkeeping
raises. The live 2026-08-12 failure was ``OSError(ENOTEMPTY)`` from
macOS ``smbfs`` over a still-open ``.lock`` handle, and that is exactly
what these tests inject.

Expected red (test-first): ``pull_model`` raises ``PullEnvError``
instead of returning, so most of these fail with that exception rather
than an assert. That raise *is* the bug. The warning tests read the
message the spec asks for — the leaf path, the errno, and wording that
says the archive is complete and only client bookkeeping remains.

Everything drives the ``FakeHubClient`` from conftest; no network, and
archives live in ``tmp_path``, never a real archive.
"""

import errno
import logging
import shutil
from pathlib import Path

import pytest
from staging_shapes import (
    Q4_BYTES,
    Q4_NAME,
    do_pull,
    failing_rmtree,
    model_dir,
    new_archive,
    staging_leaf,
)

from llm_preserver.records import load_record

# The exact tree a Q4 pull leaves under models/<creator>/<model>/;
# a cleanup that reaches into models/ would change this set.
EXPECTED_MODEL_FILES = {
    "MODEL-RECORD.md",
    "gguf/docs/bartowski--tiny-chat-GGUF/README.md",
    f"gguf/{Q4_NAME}",
    "manifest-sha256.txt",
    "model-record.json",
}


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


@pytest.fixture
def cleanup_fails(monkeypatch: pytest.MonkeyPatch, archive: Path) -> Path:
    """Make the staging-leaf removal raise the live ENOTEMPTY.

    Returns the leaf whose removal fails, so a test can name it.
    """
    leaf = staging_leaf(archive)
    monkeypatch.setattr(shutil, "rmtree", failing_rmtree(leaf))
    return leaf


def leaf_warnings(caplog: pytest.LogCaptureFixture, leaf: Path) -> list[str]:
    """WARNING-or-worse messages that name the staging leaf."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and str(leaf) in record.getMessage()
    ]


def test_pull_returns_normally_when_the_staging_cleanup_raises(
    archive, fake_hub_factory, cleanup_fails
):
    """A cleanup-only failure is not a pull failure: pull_model returns."""
    result = do_pull(archive, fake_hub_factory(base_model=None))

    assert result == model_dir(archive)


def test_record_survives_a_failed_staging_cleanup(archive, fake_hub_factory, cleanup_fails):
    """The record written before the cleanup is on disk and loadable."""
    do_pull(archive, fake_hub_factory(base_model=None))

    record = load_record(model_dir(archive))
    assert record.hub_id == "bartowski/tiny-chat-GGUF"


def test_manifest_survives_a_failed_staging_cleanup(archive, fake_hub_factory, cleanup_fails):
    """The manifest covers the payload exactly as a clean pull leaves it."""
    do_pull(archive, fake_hub_factory(base_model=None))

    manifest = (model_dir(archive) / "manifest-sha256.txt").read_text(encoding="utf-8")
    assert f"gguf/{Q4_NAME}" in manifest


def test_payload_stays_archived_when_the_staging_cleanup_fails(
    archive, fake_hub_factory, cleanup_fails
):
    """The downloaded bytes are in the archive, byte for byte."""
    do_pull(archive, fake_hub_factory(base_model=None))

    assert (model_dir(archive) / "gguf" / Q4_NAME).read_bytes() == Q4_BYTES


def test_failed_staging_cleanup_logs_exactly_one_warning(
    archive, fake_hub_factory, cleanup_fails, caplog
):
    """One WARNING, not a repeated or per-file complaint."""
    with caplog.at_level(logging.WARNING):
        do_pull(archive, fake_hub_factory(base_model=None))

    assert len(leaf_warnings(caplog, cleanup_fails)) == 1


def test_failed_staging_cleanup_warning_names_the_leaf_and_the_errno(
    archive, fake_hub_factory, cleanup_fails, caplog
):
    """The warning names the leaf path and the errno, so it is findable."""
    with caplog.at_level(logging.WARNING):
        do_pull(archive, fake_hub_factory(base_model=None))

    message = leaf_warnings(caplog, cleanup_fails)[0]
    assert str(cleanup_fails) in message
    # ENOTEMPTY is 66 on macOS and 39 on Linux, so the symbol is the
    # only portable form; the path is dropped first so a tmp directory
    # that happens to contain those digits cannot satisfy the assert.
    assert str(errno.ENOTEMPTY) in message.replace(str(cleanup_fails), "")


def test_failed_staging_cleanup_warning_says_the_archive_is_complete(
    archive, fake_hub_factory, cleanup_fails, caplog
):
    """Actionable, not alarming: complete archive, leftover bookkeeping."""
    with caplog.at_level(logging.WARNING):
        do_pull(archive, fake_hub_factory(base_model=None))

    message = leaf_warnings(caplog, cleanup_fails)[0].lower()
    assert "complete" in message
    assert "bookkeeping" in message


def test_failed_staging_cleanup_leaves_the_models_tree_exactly_as_pulled(
    archive, fake_hub_factory, cleanup_fails
):
    """Cleanup failure never touches anything under models/."""
    do_pull(archive, fake_hub_factory(base_model=None))

    found = {
        path.relative_to(model_dir(archive)).as_posix()
        for path in model_dir(archive).rglob("*")
        if path.is_file()
    }
    assert found == EXPECTED_MODEL_FILES


def test_failed_staging_cleanup_leaves_the_residue_under_staging(
    archive, fake_hub_factory, cleanup_fails
):
    """What the delete could not remove stays in .staging/, not in models/.

    The leaf is the only place a partial cleanup can leave anything, so
    the residue is exactly where ``verify --staging`` already looks.
    """
    do_pull(archive, fake_hub_factory(base_model=None))

    assert cleanup_fails.is_dir()
    assert cleanup_fails.is_relative_to(archive / ".staging")
