"""Tests for llm_preserver.records — schema v3 (spec 0017) lineage fields.

Split from test_records_v2.py (300-line rule). ADR 0003 flattens the
layout, so the lineage the old nested path asserted structurally now
lives in the record: ``base_model`` plus the source of that claim
(card-declared, curator-asserted via ``--base-model``, or harvested by
migrate from an old nested path). Three ways a lineage line can arrive,
and a record that flattens them into one field cannot be audited later.

Expected red (test-first): the fields do not exist. Note the pydantic
subtlety these tests are written around — ``_PreservingModel`` sets
``extra="allow"``, so an *undeclared* keyword is silently kept as an
extra. Reading it back therefore proves nothing; the tests below assert
the things an extra cannot fake (a default when the field is absent, a
rejected value, an explicit ``null`` on disk).
"""

import datetime
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from llm_preserver.records import (
    RECORD_FILENAME,
    RECORD_SCHEMA_VERSION,
    ArtifactEntry,
    FileEntry,
    ModelRecord,
    load_record,
    save_record,
)

FULL_COMMIT_HASH = "a" * 40
FILE_SHA256 = "0" * 64
BASE_REPO = "Qwen/Qwen3.6-27B"


def make_model_record(**overrides) -> ModelRecord:
    """A minimal valid record; overrides pass straight to the model."""
    kwargs = {
        "name": "tiny-chat",
        "hub_id": "acme/tiny-chat",
        "roles": ["chat"],
        "license": "apache-2.0",
        "artifacts": [
            ArtifactEntry(
                format="gguf",
                quantization="Q4_K_M",
                source_repo="https://huggingface.co/acme/tiny-chat",
                revision=FULL_COMMIT_HASH,
                download_date=datetime.date(2026, 8, 11),
                provenance="verified",
                files=[
                    FileEntry(
                        path="gguf/tiny-chat-Q4_K_M.gguf",
                        sha256=FILE_SHA256,
                        size=12345,
                        source="original",
                    )
                ],
            )
        ],
    }
    kwargs.update(overrides)
    return ModelRecord(**kwargs)


def test_record_schema_version_is_three() -> None:
    # hub_id changes meaning under ADR 0003 (it names the source repo,
    # not the original model) and lineage fields arrive: both are
    # reasons an older tool must not silently reinterpret a new record.
    assert RECORD_SCHEMA_VERSION == 3


def test_a_new_record_declares_schema_version_three() -> None:
    assert make_model_record().record_schema_version == 3


def test_base_model_defaults_to_none() -> None:
    # A pull records what the card declares and never invents one, so
    # "no declared lineage" must be representable.
    assert make_model_record().base_model is None


def test_base_model_source_defaults_to_none() -> None:
    assert make_model_record().base_model_source is None


@pytest.mark.parametrize("claim_source", ["card", "asserted", "migrated"])
def test_base_model_source_accepts_each_recognized_claim_source(claim_source: str) -> None:
    # The three sanctioned origins of a lineage claim (0017 non-goals:
    # the tool never guesses a base for a repo that named none).
    record = make_model_record(base_model=BASE_REPO, base_model_source=claim_source)

    assert record.base_model_source == claim_source


def test_base_model_source_rejects_an_unrecognized_claim_source() -> None:
    # Not a free string: an unattributable claim would make the
    # provenance unauditable, which is the whole point of the field.
    with pytest.raises(ValidationError):
        make_model_record(base_model=BASE_REPO, base_model_source="guessed")


def test_lineage_fields_serialize_as_explicit_nulls(tmp_path: Path) -> None:
    # The schema's standing convention (records/schema.py): nullable
    # fields are written as explicit null, never omitted, so the
    # ls-and-cat reader can tell "unknown" from "not in this version".
    save_record(make_model_record(), tmp_path)

    on_disk = json.loads((tmp_path / "model-record.json").read_text(encoding="utf-8"))

    assert on_disk["base_model"] is None
    assert on_disk["base_model_source"] is None


def test_lineage_survives_a_save_and_load_round_trip(tmp_path: Path) -> None:
    save_record(make_model_record(base_model=BASE_REPO, base_model_source="migrated"), tmp_path)

    loaded = load_record(tmp_path)

    assert (loaded.base_model, loaded.base_model_source) == (BASE_REPO, "migrated")


def test_v2_record_still_loads_with_unknown_lineage(tmp_path: Path) -> None:
    # Pure widening (ADR 0001's add-rather-than-rename conservatism): a
    # record written by the v2 tool loads untouched, its lineage
    # reading as unknown rather than as a claim nobody made.
    v2 = {
        "record_schema_version": 2,
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
                "source_repo": "https://huggingface.co/other/tiny-chat-GGUF",
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
                        "provenance": "verified",
                    }
                ],
            }
        ],
    }
    (tmp_path / "model-record.json").write_text(json.dumps(v2), encoding="utf-8")

    loaded = load_record(tmp_path)

    assert loaded.record_schema_version == 2
    assert loaded.base_model is None
    assert loaded.base_model_source is None


def test_base_model_source_without_a_base_model_is_rejected() -> None:
    # An attribution with nothing attributed: the record would claim a
    # card declared a lineage that is not there, which is exactly the
    # unauditable provenance the field pair exists to prevent.
    with pytest.raises(ValidationError):
        make_model_record(base_model_source="card")


@pytest.mark.parametrize(
    "claim",
    ["../../../etc/passwd", "noslash", "acme/", "/repo", "acme/a/b", "acme/bad repo"],
)
def test_base_model_must_be_a_usable_repo_id(claim: str) -> None:
    # The value is rendered into MODEL-RECORD.md and, from pass 4,
    # composed into commands — it cannot be free text.
    with pytest.raises(ValidationError):
        make_model_record(base_model=claim, base_model_source="asserted")


def test_a_written_record_claims_the_current_schema(tmp_path: Path) -> None:
    """Live-use finding 2026-08-11: a re-pull merged spec 0017's
    ``base_model`` into a v2 record and saved it still claiming v2 —
    exactly backwards for the field that tells an older tool to warn.
    Serializing emits every field this version knows, so the bytes on
    disk are the current shape and the number must say so."""
    stale = make_model_record()
    stale.record_schema_version = 2

    save_record(stale, tmp_path)

    on_disk = json.loads((tmp_path / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["record_schema_version"] == RECORD_SCHEMA_VERSION
