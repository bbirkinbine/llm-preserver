"""Tests for llm_preserver.records — schema v2 (spec 0003) additions.

Split from test_records.py (300-line rule): per-file provenance and
the "hashed-locally" state, artifact-provenance derivation, v1-record
compatibility, and concurrent-write safety.
"""

import datetime
import json

import pytest
from pydantic import ValidationError

from llm_preserver.records import (
    ArtifactEntry,
    FileEntry,
    ModelRecord,
    derive_artifact_provenance,
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


def test_artifact_provenance_accepts_hashed_locally():
    # Spec 0003: bytes came straight from the hub and our SHA256 is
    # recorded, but the hub published no hash to check against.
    entry = make_artifact_entry(provenance="hashed-locally")
    assert entry.provenance == "hashed-locally"


def test_file_entry_provenance_defaults_to_none():
    # v2 adds optional per-file provenance; absent means unknown.
    assert make_file_entry().provenance is None


def test_file_entry_rejects_invalid_provenance():
    with pytest.raises(ValidationError):
        make_file_entry(provenance="probably-fine")


def test_per_file_provenance_round_trips(tmp_path):
    record = make_model_record(
        artifacts=[
            make_artifact_entry(
                provenance="hashed-locally",
                files=[
                    make_file_entry(provenance="verified"),
                    make_file_entry(path="gguf/README.md", sha256="1" * 64, size=10),
                ],
            )
        ],
    )
    save_record(record, tmp_path)
    files = load_record(tmp_path).artifacts[0].files
    assert files[0].provenance == "verified"
    assert files[1].provenance is None


def test_derive_artifact_provenance_all_verified_is_verified():
    files = [
        make_file_entry(provenance="verified"),
        make_file_entry(path="gguf/b.gguf", provenance="verified"),
    ]
    assert derive_artifact_provenance(files) == "verified"


def test_derive_artifact_provenance_demotes_on_any_unverified_file():
    files = [
        make_file_entry(provenance="verified"),
        make_file_entry(path="gguf/README.md", provenance="hashed-locally"),
    ]
    assert derive_artifact_provenance(files) == "hashed-locally"


def test_v1_record_still_loads(tmp_path):
    # Backward compatibility: a record written by the v1 tool (nonempty
    # roles, no per-file provenance) loads under the v2 schema, with
    # per-file provenance reading as unknown.
    v1 = {
        "record_schema_version": 1,
        "name": "tiny-chat",
        "hub_id": "acme/tiny-chat",
        "roles": ["chat"],
        "capabilities": None,
        "pipeline_tag": None,
        "license": "apache-2.0",
        "parameter_count": None,
        "context_length": None,
        "notes": None,
        "artifacts": [
            {
                "format": "gguf",
                "quantization": "Q4_K_M",
                "source_repo": None,
                "revision": FULL_COMMIT_HASH,
                "download_date": None,
                "runtime_tested": None,
                "provenance": "verified",
                "files": [
                    {
                        "path": "gguf/tiny-chat-Q4_K_M.gguf",
                        "sha256": FILE_SHA256,
                        "size": 12345,
                        "source": "original",
                    }
                ],
            }
        ],
    }
    (tmp_path / "model-record.json").write_text(json.dumps(v1))
    loaded = load_record(tmp_path)
    assert loaded.roles == ["chat"]
    assert loaded.artifacts[0].files[0].provenance is None


def test_record_rewrite_is_last_write_wins_safe(tmp_path):
    # Two concurrent pulls into one model could race on the record
    # (spec 0003 Notes): no lock, but the write path must stay
    # last-write-wins safe — the second save wins cleanly and the file
    # stays valid, never interleaved or truncated.
    save_record(make_model_record(), tmp_path)
    first = load_record(tmp_path)
    second = load_record(tmp_path)
    first.notes = "writer one"
    second.notes = "writer two"

    save_record(first, tmp_path)
    save_record(second, tmp_path)

    final = load_record(tmp_path)
    assert final.notes == "writer two"
    json.loads((tmp_path / "model-record.json").read_text())  # still valid JSON
