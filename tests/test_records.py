"""Tests for llm_preserver.records — record schema and JSON round-trip.

Pins the API surface from the spec-0001 plan: FileEntry, ArtifactEntry,
ModelRecord (Pydantic v2), plus load_record/save_record against a model
dir's model-record.json.
"""

import datetime
import json

import pytest
from pydantic import ValidationError

from llm_preserver.records import (
    ArtifactEntry,
    FileEntry,
    ModelRecord,
    load_record,
    save_record,
)

FULL_COMMIT_HASH = "a" * 40
FILE_SHA256 = "0" * 64


def make_file_entry(**overrides) -> FileEntry:
    kwargs = {
        "path": "gguf/tiny-chat-Q4_K_M.gguf",
        "sha256": FILE_SHA256,
        "size": 12345,
        "source": "original",
    }
    kwargs.update(overrides)
    return FileEntry(**kwargs)


def make_artifact_entry(**overrides) -> ArtifactEntry:
    kwargs = {
        "format": "gguf",
        "quantization": "Q4_K_M",
        "source_repo": "https://huggingface.co/other/tiny-chat-GGUF",
        "revision": FULL_COMMIT_HASH,
        "download_date": datetime.date(2026, 7, 9),
        "runtime_tested": "llama.cpp b4000",
        "provenance": "verified",
        "files": [make_file_entry()],
    }
    kwargs.update(overrides)
    return ArtifactEntry(**kwargs)


def make_model_record(**overrides) -> ModelRecord:
    kwargs = {
        "name": "tiny-chat",
        "hub_id": "acme/tiny-chat",
        "roles": ["chat"],
        "license": "apache-2.0",
        "parameter_count": "1B",
        "context_length": 4096,
        "notes": None,
        "artifacts": [make_artifact_entry()],
    }
    kwargs.update(overrides)
    return ModelRecord(**kwargs)


def test_file_entry_allows_unknown_sha256_and_size():
    entry = make_file_entry(sha256=None, size=None)
    assert entry.sha256 is None
    assert entry.size is None


def test_rejects_invalid_role():
    with pytest.raises(ValidationError):
        make_model_record(roles=["poetry"])


def test_rejects_empty_roles():
    with pytest.raises(ValidationError):
        make_model_record(roles=[])


def test_accepts_multiple_roles():
    record = make_model_record(roles=["chat", "coding"])
    assert record.roles == ["chat", "coding"]


def test_rejects_branch_name_as_revision():
    with pytest.raises(ValidationError):
        make_artifact_entry(revision="main")


def test_accepts_full_commit_hash_revision():
    entry = make_artifact_entry(revision=FULL_COMMIT_HASH)
    assert entry.revision == FULL_COMMIT_HASH


def test_revision_may_be_unknown():
    entry = make_artifact_entry(revision=None, provenance="unverified")
    assert entry.revision is None


def test_rejects_invalid_provenance_flag():
    with pytest.raises(ValidationError):
        make_artifact_entry(provenance="probably-fine")


def test_rejects_invalid_artifact_format():
    with pytest.raises(ValidationError):
        make_artifact_entry(format="safetensors-zip")


def test_rejects_absolute_file_path():
    with pytest.raises(ValidationError):
        make_file_entry(path="/etc/passwd")


def test_rejects_parent_traversal_file_path():
    with pytest.raises(ValidationError):
        make_file_entry(path="../escape.gguf")


def test_rejects_control_characters_in_file_path():
    with pytest.raises(ValidationError):
        make_file_entry(path="a.gguf |\n| forged-row.gguf")


def test_round_trip_through_model_dir_is_lossless(tmp_path):
    record = make_model_record(
        license=None,
        notes="pulled for offline coding",
        artifacts=[
            make_artifact_entry(
                revision=None,
                download_date=None,
                provenance="unverified",
                files=[make_file_entry(sha256=None, size=None)],
            )
        ],
    )
    save_record(record, tmp_path)
    loaded = load_record(tmp_path)
    assert loaded == record


def test_save_writes_model_record_json_in_model_dir(tmp_path):
    save_record(make_model_record(), tmp_path)
    assert (tmp_path / "model-record.json").is_file()


def test_save_writes_generated_markdown_alongside_json(tmp_path):
    save_record(make_model_record(), tmp_path)
    markdown = (tmp_path / "MODEL-RECORD.md").read_text()
    assert "generated" in markdown.lower()
    assert "tiny-chat" in markdown


def test_record_json_carries_schema_version(tmp_path):
    save_record(make_model_record(), tmp_path)
    data = json.loads((tmp_path / "model-record.json").read_text())
    assert data["record_schema_version"] == 1


def test_unknown_fields_survive_round_trip(tmp_path):
    # A record written by a newer tool version must not lose its
    # extra fields when this version rewrites it.
    record = make_model_record()
    data = json.loads(record.model_dump_json())
    data["future_field"] = "keep me"
    (tmp_path / "model-record.json").write_text(json.dumps(data))

    loaded = load_record(tmp_path)
    save_record(loaded, tmp_path)

    rewritten = json.loads((tmp_path / "model-record.json").read_text())
    assert rewritten["future_field"] == "keep me"


def test_null_fields_are_written_to_json_not_omitted(tmp_path):
    record = make_model_record(
        license=None,
        artifacts=[make_artifact_entry(files=[make_file_entry(sha256=None, size=None)])],
    )
    save_record(record, tmp_path)
    data = json.loads((tmp_path / "model-record.json").read_text())
    assert "license" in data
    assert data["license"] is None
    file_entry = data["artifacts"][0]["files"][0]
    assert "sha256" in file_entry
    assert file_entry["sha256"] is None
    assert "size" in file_entry
    assert file_entry["size"] is None
    assert "capabilities" in data
    assert data["capabilities"] is None
    assert "pipeline_tag" in data
    assert data["pipeline_tag"] is None
