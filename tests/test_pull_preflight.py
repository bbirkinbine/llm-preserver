"""Unit tests for llm_preserver.pull_preflight (spec 0004).

Pins the spec-0004 plan seam — the disk preflight that refuses a
whole-tree pull before any bytes download:

- ``total_selected_size(files)`` → ``(total_bytes, file_count)``;
  files with no hub-reported size are excluded from the sum but still
  counted.
- ``human_size(n)`` — human-readable binary units for prompts/errors.
- ``require_disk_space(archive_root, needed)`` — compares against
  ``shutil.disk_usage`` free space; insufficient space is a
  local-environment fault naming required vs. available.

The module import is deliberately top-level: until the module exists,
this whole file fails collection (the expected red).
"""

import shutil
from collections import namedtuple

import pytest

import llm_preserver.pull_preflight as pull_preflight
from llm_preserver.hub import PullEnvError, RepoFile

DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


def repo_file(path, size=None, sha256=None):
    return RepoFile(path=path, size=size, sha256=sha256)


def test_total_selected_size_sums_sizes_and_counts_files():
    files = [repo_file("a.safetensors", 100), repo_file("b.safetensors", 50)]
    assert pull_preflight.total_selected_size(files) == (150, 2)


def test_none_sizes_are_excluded_from_sum_but_still_counted():
    files = [
        repo_file("a.safetensors", 100),
        repo_file("tokenizer.json", None),
        repo_file("b.safetensors", 50),
    ]
    assert pull_preflight.total_selected_size(files) == (150, 3)


def test_empty_selection_totals_zero_bytes_and_zero_files():
    assert pull_preflight.total_selected_size([]) == (0, 0)


def test_human_size_renders_small_byte_counts_verbatim():
    assert pull_preflight.human_size(500) == "500 B"


def test_human_size_scales_to_binary_units():
    assert pull_preflight.human_size(1536) == "1.5 KiB"
    assert pull_preflight.human_size(50 * 1024**3) == "50.0 GiB"


def test_insufficient_space_raises_env_error_naming_required_and_available(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: DiskUsage(10**12, 10**12 - 1024, 1024))

    with pytest.raises(PullEnvError) as excinfo:
        pull_preflight.require_disk_space(tmp_path, 50 * 1024**3)

    message = str(excinfo.value)
    assert "50.0 GiB" in message  # required
    assert "1.0 KiB" in message  # available


def test_sufficient_space_passes_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: DiskUsage(10**12, 0, 10**12))

    pull_preflight.require_disk_space(tmp_path, 50 * 1024**3)
