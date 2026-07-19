"""Core tests for ``staging_leftovers`` (spec 0012): hash-free scan.

The primitive lives in ``model_scan.py`` alongside ``unrecorded_files``
so ``verify`` and any future caller cannot disagree about what a
"leftover" is. These tests pin the scan shape only — no CLI, no
hashing, no record loading — over plain tmp dirs.

Expected red (test-first): ``staging_leftovers`` / ``StagingLeftover``
do not exist yet, so this module fails to import until the feature
lands. That ImportError is the intended failure mode.
"""

from pathlib import Path

import pytest

from llm_preserver.archive import ArchiveError
from llm_preserver.model_scan import StagingLeftover, staging_leftovers
from llm_preserver.pull_prepare import STAGING_DIRNAME


def _leaf(root: Path, creator: str, model: str) -> Path:
    """Create ``root/.staging/<creator>/<model>/`` and return it."""
    leaf = root / STAGING_DIRNAME / creator / model
    leaf.mkdir(parents=True)
    return leaf


def test_non_empty_leaf_returns_id_bytes_and_count(tmp_path: Path) -> None:
    """One leftover: id, summed on-disk bytes, and regular-file count."""
    leaf = _leaf(tmp_path, "acme", "tiny-chat")
    (leaf / "a.incomplete").write_bytes(b"x" * 100)
    (leaf / "b.incomplete").write_bytes(b"y" * 250)

    result = staging_leftovers(tmp_path)

    assert len(result) == 1
    entry = result[0]
    assert isinstance(entry, StagingLeftover)
    assert entry.model_id == "acme/tiny-chat"
    assert entry.path == leaf
    assert entry.total_bytes == 350
    assert entry.file_count == 2


def test_leaf_without_regular_file_is_skipped(tmp_path: Path) -> None:
    """An empty leaf, or one holding only empty subdirs, is not a leftover."""
    (tmp_path / STAGING_DIRNAME / "acme" / "empty").mkdir(parents=True)
    (tmp_path / STAGING_DIRNAME / "beta" / "only-dirs" / "nested").mkdir(parents=True)

    assert staging_leftovers(tmp_path) == []


def test_multiple_leftovers_sorted_by_model_id(tmp_path: Path) -> None:
    """Leftovers come back sorted by ``<creator>/<model>`` id."""
    for creator in ("zeta", "acme", "beta"):
        leaf = _leaf(tmp_path, creator, "model")
        (leaf / "part.incomplete").write_bytes(b"x" * 10)

    result = staging_leftovers(tmp_path)

    assert [entry.model_id for entry in result] == ["acme/model", "beta/model", "zeta/model"]


def test_missing_staging_dir_returns_empty(tmp_path: Path) -> None:
    """No ``.staging/`` at all is an empty list, not an error."""
    assert staging_leftovers(tmp_path) == []


def test_symlinked_staging_container_raises_archive_error(tmp_path: Path) -> None:
    """A symlinked ``.staging/`` is refused, as ``models/`` is (iter_model_dirs)."""
    real = tmp_path / "real-staging"
    leaf = real / "acme" / "model"
    leaf.mkdir(parents=True)
    (leaf / "part.incomplete").write_bytes(b"x" * 10)
    (tmp_path / STAGING_DIRNAME).symlink_to(real, target_is_directory=True)

    with pytest.raises(ArchiveError):
        staging_leftovers(tmp_path)


def test_symlinked_creator_and_leaf_dirs_are_skipped(tmp_path: Path) -> None:
    """A creator dir or a leaf reached through a symlink is never followed."""
    staging = tmp_path / STAGING_DIRNAME
    real = staging / "good" / "real-model"
    real.mkdir(parents=True)
    (real / "part.incomplete").write_bytes(b"x" * 50)
    # An outside tree the symlinks would reach if followed.
    outside = tmp_path / "outside"
    (outside / "model").mkdir(parents=True)
    (outside / "model" / "part.incomplete").write_bytes(b"y" * 70)
    (staging / "evil-creator").symlink_to(outside, target_is_directory=True)
    (staging / "good" / "evil-model").symlink_to(outside / "model", target_is_directory=True)

    result = staging_leftovers(tmp_path)

    assert [entry.model_id for entry in result] == ["good/real-model"]


def test_hf_cache_bookkeeping_is_counted(tmp_path: Path) -> None:
    """The whole leaf counts — hf's own .cache/ bookkeeping included.

    The scan surfaces all incidental staging space a verify can't see
    (spec 0012); the human decides what to do with it. So hf's local-dir
    bookkeeping is counted, not filtered — classifying payload vs
    hf-internal would depend on hf's cache layout and could hide bytes.
    """
    leaf = _leaf(tmp_path, "acme", "model")
    (leaf / "model-00001.safetensors").write_bytes(b"x" * 100)  # a completed shard
    cache = leaf / ".cache" / "huggingface" / "download"
    cache.mkdir(parents=True)
    (cache / "model-00002.safetensors.incomplete").write_bytes(b"y" * 50)  # in-progress bytes
    (cache / "model-00001.safetensors.metadata").write_bytes(b"z" * 10)  # hf sidecar

    result = staging_leftovers(tmp_path)

    assert len(result) == 1
    entry = result[0]
    assert entry.total_bytes == 160  # 100 + 50 + 10: the whole leaf, hf cache included
    assert entry.file_count == 3


def test_leaf_with_only_hf_cache_is_still_flagged(tmp_path: Path) -> None:
    """A pull interrupted before any shard completed — only .cache/ — still counts.

    A single large file interrupted mid-download has all its bytes in
    ``.cache/…/*.incomplete`` and nothing at a payload path; the leftover
    must not vanish.
    """
    leaf = _leaf(tmp_path, "acme", "model")
    cache = leaf / ".cache" / "huggingface" / "download"
    cache.mkdir(parents=True)
    (cache / "big-quant.gguf.incomplete").write_bytes(b"y" * 15000)

    result = staging_leftovers(tmp_path)

    assert len(result) == 1
    assert result[0].total_bytes == 15000  # the abandoned in-progress bytes are surfaced
    assert result[0].file_count == 1


def test_symlinked_file_skipped_nested_real_subdir_summed(tmp_path: Path) -> None:
    """Bytes sum real regular files, descend real subdirs, skip symlinks."""
    leaf = _leaf(tmp_path, "acme", "model")
    (leaf / "sub").mkdir()
    (leaf / "a.bin").write_bytes(b"x" * 100)
    (leaf / "sub" / "b.bin").write_bytes(b"y" * 40)  # a nested real subdir counts
    outside_file = tmp_path / "outside.bin"
    outside_file.write_bytes(b"z" * 999)
    (leaf / "link.bin").symlink_to(outside_file)  # symlinked file: not counted
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "c.bin").write_bytes(b"w" * 500)
    (leaf / "evildir").symlink_to(outside_dir, target_is_directory=True)  # not descended

    result = staging_leftovers(tmp_path)

    assert len(result) == 1
    entry = result[0]
    assert entry.total_bytes == 140  # 100 + 40 only
    assert entry.file_count == 2  # a.bin + sub/b.bin only
