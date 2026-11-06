"""Tests for llm_preserver.records — schema v4 (issue #69) media roles.

v4 widens the ``Role`` vocabulary with ``diffusion``, ``video`` and
``image``. The bump is what lets an older tool explain itself: a v3 tool
rejects a media role as an unknown enum value, and only a record that
claims a newer schema turns that ``record unreadable`` into "written by
a newer tool" rather than apparent corruption. The previous enum
widening (``hashed-locally``, v1 -> v2) bumped for the same reason.

A widening, not a migration: v3 records load untouched and nothing on
disk is rewritten until a later write stamps the current version.
"""

import json
from pathlib import Path

import pytest

from llm_preserver import archive
from llm_preserver.archive import init_archive, inventory
from llm_preserver.records import (
    RECORD_FILENAME,
    RECORD_SCHEMA_VERSION,
    ModelRecord,
    load_record,
    save_record,
)

MEDIA_ROLES = ["diffusion", "video", "image"]


def test_record_schema_version_is_four() -> None:
    assert RECORD_SCHEMA_VERSION == 4


def test_a_new_record_declares_schema_version_four(sample_record_dict) -> None:
    assert ModelRecord(**sample_record_dict()).record_schema_version == 4


@pytest.mark.parametrize("role", MEDIA_ROLES)
def test_a_saved_media_role_record_claims_v4_on_disk(
    tmp_path: Path, sample_record_dict, role: str
) -> None:
    save_record(ModelRecord(**sample_record_dict(roles=[role])), tmp_path)

    on_disk = json.loads((tmp_path / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["record_schema_version"] == 4


def test_v3_record_still_loads_unchanged(tmp_path: Path, sample_record_dict) -> None:
    v3 = sample_record_dict(record_schema_version=3, roles=["chat", "multimodal"])
    (tmp_path / RECORD_FILENAME).write_text(json.dumps(v3), encoding="utf-8")
    before = (tmp_path / RECORD_FILENAME).read_bytes()

    loaded = load_record(tmp_path)

    assert loaded.record_schema_version == 3
    assert loaded.roles == ["chat", "multimodal"]
    assert (tmp_path / RECORD_FILENAME).read_bytes() == before


@pytest.mark.parametrize("role", MEDIA_ROLES)
def test_v3_tool_reads_a_media_role_record_as_newer_not_corrupt(
    tmp_path: Path, write_model, sample_record_dict, monkeypatch, role: str
) -> None:
    # Simulate the v3 tool: it rejects a role it does not know (stand-in
    # value, since this tool now accepts the real ones) and compares the
    # record's claim against its own version 3.
    init_archive(tmp_path)
    model_dir = write_model(tmp_path, record=None)
    save_record(ModelRecord(**sample_record_dict(roles=[role])), model_dir)
    data = json.loads((model_dir / RECORD_FILENAME).read_text(encoding="utf-8"))
    data["roles"] = ["role-unknown-to-v3"]
    (model_dir / RECORD_FILENAME).write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(archive, "RECORD_SCHEMA_VERSION", 3)

    (summary,) = inventory(tmp_path)

    assert summary.record_error is True
    assert summary.newer_record_schema is True
