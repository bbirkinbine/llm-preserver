"""Tests for llm_preserver.archive — init skeleton and inventory walk.

Layout and marker semantics come from ADR 0001: models/, runtimes/,
manifests/, and a versioned archive.json root marker (schema_version 1).
"""

import json

import pytest

from llm_preserver.archive import ArchiveError, init_archive, inventory


def test_init_creates_skeleton_dirs(tmp_path):
    init_archive(tmp_path)
    assert (tmp_path / "models").is_dir()
    assert (tmp_path / "runtimes").is_dir()
    assert (tmp_path / "manifests").is_dir()


def test_init_writes_versioned_root_marker(tmp_path):
    init_archive(tmp_path)
    marker = json.loads((tmp_path / "archive.json").read_text())
    assert marker["schema_version"] == 1


def test_init_rerun_on_existing_archive_changes_nothing(tmp_path):
    init_archive(tmp_path)
    sentinel = tmp_path / "models" / "keep.txt"
    sentinel.write_text("existing content")
    marker_before = (tmp_path / "archive.json").read_bytes()

    init_archive(tmp_path)

    assert sentinel.read_text() == "existing content"
    assert (tmp_path / "archive.json").read_bytes() == marker_before


def test_init_refuses_nonempty_dir_that_is_not_an_archive(tmp_path):
    stray = tmp_path / "somefile.txt"
    stray.write_text("not an archive")

    with pytest.raises(ArchiveError):
        init_archive(tmp_path)

    assert stray.read_text() == "not an archive"
    assert not (tmp_path / "models").exists()
    assert not (tmp_path / "archive.json").exists()


def test_init_refuses_newer_schema_version(tmp_path):
    for name in ("models", "runtimes", "manifests"):
        (tmp_path / name).mkdir()
    (tmp_path / "archive.json").write_text(json.dumps({"schema_version": 99}))

    with pytest.raises(ArchiveError):
        init_archive(tmp_path)


def test_inventory_of_empty_archive_lists_no_models(tmp_path):
    init_archive(tmp_path)
    assert inventory(tmp_path) == []


def test_inventory_reports_formats_and_role(tmp_path, write_model, sample_record_dict):
    init_archive(tmp_path)
    write_model(tmp_path, sample_record_dict())

    (summary,) = inventory(tmp_path)

    assert summary.model_id == "acme/tiny-chat"
    assert summary.formats == ["gguf"]
    assert summary.roles == ["chat"]


def test_inventory_complete_record_has_no_missing_flags(tmp_path, write_model, sample_record_dict):
    init_archive(tmp_path)
    write_model(tmp_path, sample_record_dict())

    (summary,) = inventory(tmp_path)

    assert summary.missing_record is False
    assert summary.missing_license is False
    assert summary.missing_checksums is False


def test_inventory_sums_sizes_from_record_not_disk(tmp_path, write_model, sample_record_dict):
    # The payload file listed in the record is never created on disk:
    # sizes must come from record entries, not from stat/hashing.
    init_archive(tmp_path)
    write_model(tmp_path, sample_record_dict())

    (summary,) = inventory(tmp_path)

    assert summary.total_size == 12345


def test_inventory_flags_missing_license(tmp_path, write_model, sample_record_dict):
    init_archive(tmp_path)
    write_model(tmp_path, sample_record_dict(license=None))

    (summary,) = inventory(tmp_path)

    assert summary.missing_license is True


def test_inventory_flags_missing_checksums(tmp_path, write_model, sample_record_dict):
    record = sample_record_dict()
    record["artifacts"][0]["files"][0]["sha256"] = None
    init_archive(tmp_path)
    write_model(tmp_path, record)

    (summary,) = inventory(tmp_path)

    assert summary.missing_checksums is True


def test_inventory_lists_recordless_model_dir_as_incomplete(tmp_path, write_model):
    init_archive(tmp_path)
    write_model(tmp_path, record=None)

    (summary,) = inventory(tmp_path)

    assert summary.model_id == "acme/tiny-chat"
    assert summary.missing_record is True


def test_inventory_surfaces_corrupt_record_as_error_not_crash(tmp_path, write_model):
    init_archive(tmp_path)
    model_dir = write_model(tmp_path, record=None)
    (model_dir / "model-record.json").write_text("{ this is not json")

    (summary,) = inventory(tmp_path)

    assert summary.model_id == "acme/tiny-chat"
    assert summary.record_error is True


def test_inventory_flags_newer_record_schema(tmp_path, write_model, sample_record_dict):
    init_archive(tmp_path)
    write_model(tmp_path, sample_record_dict(record_schema_version=99))

    (summary,) = inventory(tmp_path)

    assert summary.record_error is False
    assert summary.newer_record_schema is True


def test_inventory_marks_newer_schema_on_unreadable_record(
    tmp_path, write_model, sample_record_dict
):
    # A newer tool may use enum values this version rejects; the error
    # state should carry the newer-schema hint, not read as corruption.
    record = sample_record_dict(record_schema_version=99, roles=["chat", "brand-new-role"])
    init_archive(tmp_path)
    write_model(tmp_path, record)

    (summary,) = inventory(tmp_path)

    assert summary.record_error is True
    assert summary.newer_record_schema is True


def test_inventory_skips_symlinked_model_dir(tmp_path):
    init_archive(tmp_path)
    outside = tmp_path / "outside-model"
    outside.mkdir()
    creator_dir = tmp_path / "models" / "acme"
    creator_dir.mkdir(parents=True)
    (creator_dir / "tiny-chat").symlink_to(outside)

    assert inventory(tmp_path) == []


def test_inventory_flags_symlinked_record_as_error(tmp_path, write_model):
    init_archive(tmp_path)
    model_dir = write_model(tmp_path, record=None)
    real = tmp_path / "real-record.json"
    real.write_text("{}")
    (model_dir / "model-record.json").symlink_to(real)

    (summary,) = inventory(tmp_path)

    assert summary.record_error is True


def test_inventory_flags_oversize_record_as_error(tmp_path, write_model):
    init_archive(tmp_path)
    model_dir = write_model(tmp_path, record=None)
    (model_dir / "model-record.json").write_text("x" * 1_100_000)

    (summary,) = inventory(tmp_path)

    assert summary.record_error is True


def test_inventory_refuses_symlinked_models_root(tmp_path):
    archive = tmp_path / "arch"
    archive.mkdir()
    init_archive(archive)
    real_models = tmp_path / "real-models"
    real_models.mkdir()
    (archive / "models").rmdir()
    (archive / "models").symlink_to(real_models)

    with pytest.raises(ArchiveError):
        inventory(archive)


def test_init_refuses_symlinked_marker(tmp_path):
    archive = tmp_path / "arch"
    archive.mkdir()
    init_archive(archive)
    real_marker = tmp_path / "real-archive.json"
    (archive / "archive.json").rename(real_marker)
    (archive / "archive.json").symlink_to(real_marker)

    with pytest.raises(ArchiveError):
        init_archive(archive)
