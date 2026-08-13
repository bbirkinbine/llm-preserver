"""Spec 0019: an emptied ``.staging/<creator>/`` goes with the leaf.

Removal is ``os.rmdir``, never ``rmtree`` (the spec 0017 rule): the
refusal ``os.rmdir`` raises on a non-empty directory is the safety
property being relied on, so a sibling model's staged bytes are never
at risk. A failure to remove the creator directory is silent — an empty
directory is not worth a warning.

Every case here drives the *successful pull* path, the only path that
deletes a staging leaf (the no-op clear drafted for this spec was cut
at review: hf writes its ``.cache/`` scaffolding before the first
network call, so a concurrent pull's live leaf reads as disposable).

The sibling test is the mutation proof for ``os.rmdir`` over
``rmtree``: swap the call and the sibling's staged bytes disappear
with the parent.
"""

import logging
from pathlib import Path

import pytest
from staging_shapes import (
    creator_dir,
    do_pull,
    new_archive,
    staging_leaf,
)


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


def sibling_leaf(archive: Path) -> Path:
    """Another model of the same creator, mid-download in staging."""
    leaf = creator_dir(archive) / "tiny-coder-GGUF"
    leaf.mkdir(parents=True, exist_ok=True)
    (leaf / "tiny-coder-Q4_K_M.gguf.incomplete").write_bytes(b"y" * 2048)
    return leaf


def test_successful_pull_removes_the_emptied_creator_directory(archive, fake_hub_factory):
    """A pull that drains its own leaf takes the empty parent with it."""
    do_pull(archive, fake_hub_factory(base_model=None))

    assert not staging_leaf(archive).exists()
    assert not creator_dir(archive).exists()


def test_creator_directory_holding_another_model_survives_the_cleanup(archive, fake_hub_factory):
    """A sibling's staged bytes are never at risk: os.rmdir, not rmtree."""
    sibling = sibling_leaf(archive)

    do_pull(archive, fake_hub_factory(base_model=None))

    assert not staging_leaf(archive).exists()  # this pull's own leaf went
    assert sibling.is_dir()
    assert (sibling / "tiny-coder-Q4_K_M.gguf.incomplete").read_bytes() == b"y" * 2048
    assert creator_dir(archive).is_dir()


def test_creator_directory_that_cannot_be_removed_warns_about_nothing(
    archive, fake_hub_factory, caplog
):
    """A parent that cannot be removed is silent — not worth a warning."""
    sibling_leaf(archive)  # keeps the parent non-empty, so its rmdir fails

    with caplog.at_level(logging.WARNING):
        do_pull(archive, fake_hub_factory(base_model=None))

    assert not staging_leaf(archive).exists()  # the leaf half still happened
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    # The parent path is a prefix of the leaf path, so a leaf warning
    # would match it; only messages about the parent alone count here.
    parent, leaf = str(creator_dir(archive)), str(staging_leaf(archive))
    assert [m for m in warnings if parent in m and leaf not in m] == []
