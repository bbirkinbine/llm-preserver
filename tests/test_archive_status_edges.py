"""Absent, conflicting, unreadable and unsafe archive evidence (spec 0023)."""

import json
from pathlib import Path
from typing import Any  # pytest fixture factories have flexible call signatures.

import pytest
from test_archive_status import HASH, REPO, artifact, file_entry, info_for, populate

from llm_preserver import archive_status
from llm_preserver.hub import RepoFile


@pytest.mark.parametrize(
    ("payload", "record_size", "hub_size", "hub_hash", "expected"),
    [
        (None, 3, 3, HASH, "missing"),
        (b"ab", 3, 3, HASH, "local-mismatch"),
        (b"abc", 3, 4, HASH, "upstream-changed"),
        (b"abc", 3, 3, "b" * 64, "upstream-changed"),
        (b"abc", None, None, None, "unavailable"),
        (b"abc", 3, None, None, "unavailable"),
        (b"ab", None, 3, HASH, "local-mismatch"),
    ],
)
def test_nonmatching_evidence_never_claims_archived(
    tmp_path: Path,
    sample_record_dict: Any,
    write_model: Any,
    payload: bytes | None,
    record_size: int | None,
    hub_size: int | None,
    hub_hash: str | None,
    expected: str,
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf", size=record_size)),
        payloads={} if payload is None else {"gguf/a.gguf": payload},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", hub_size, hub_hash))
    )
    assert state.files["a.gguf"].state == expected


@pytest.mark.parametrize("source", ["another/model", "acme/tiny-chat-other"])
def test_foreign_source_cannot_earn_archive_credit(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, source: str
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf"), source=source),
        payloads={"gguf/a.gguf": b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "not-archived"


def test_source_less_record_with_other_hub_id_does_not_match(
    tmp_path: Path, sample_record_dict: Any, write_model: Any
) -> None:
    directory = populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf"), source=None),
        payloads={"gguf/a.gguf": b"abc"},
    )
    record_path = directory / "model-record.json"
    record = json.loads(record_path.read_text())
    record["hub_id"] = "another/model"
    record_path.write_text(json.dumps(record))
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "not-archived"


@pytest.mark.parametrize("path", ["gguf/a.gguf", "runtime/a.gguf", ".staging/a.gguf"])
def test_unrecorded_payloads_never_count_even_if_bytes_exist(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, path: str
) -> None:
    populate(tmp_path, sample_record_dict, write_model, payloads={path: b"abc"})
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "not-archived"


@pytest.mark.parametrize("path", ["runtime/a.gguf", ".staging/a.gguf"])
def test_recorded_nonpayload_locations_do_not_match(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, path: str
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry(path)),
        payloads={path: b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "not-archived"


def test_corrupt_record_is_unavailable_instead_of_empty_archive(
    tmp_path: Path, write_model: Any
) -> None:
    directory = write_model(tmp_path, None)
    (directory / "model-record.json").write_text("{broken")
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.unavailable_reason
    assert state.files["a.gguf"].state == "unavailable"


def test_permission_failure_is_unavailable_instead_of_missing(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf")),
        payloads={"gguf/a.gguf": b"abc"},
    )
    original_stat = Path.stat

    def denied(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == directory / "gguf/a.gguf":
            raise PermissionError("unreadable payload metadata")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "unavailable"


@pytest.mark.parametrize("component", ["payload", "format", "record", "model"])
def test_symlinked_evidence_is_not_followed_outside_archive(
    tmp_path: Path,
    sample_record_dict: Any,
    write_model: Any,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
) -> None:
    archive = tmp_path / "archive"
    directory = populate(
        archive,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf")),
        payloads={"gguf/a.gguf": b"abc"},
    )
    target = {
        "payload": directory / "gguf/a.gguf",
        "format": directory / "gguf",
        "record": directory / "model-record.json",
        "model": directory,
    }[component]
    outside = tmp_path / "outside"
    target.rename(outside)
    target.symlink_to(outside, target_is_directory=outside.is_dir())
    original_open = Path.open

    def no_external_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert path.resolve().is_relative_to(archive), "followed external evidence"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_external_open)
    state = archive_status.snapshot_archive_status(
        archive, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "unavailable"


@pytest.mark.parametrize("path", ["../a.gguf", "/a.gguf", "bad\\a.gguf", "bad\na.gguf"])
def test_unsafe_hub_paths_are_unavailable_without_interrupting_other_files(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, path: str
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/safe.gguf")),
        payloads={"gguf/safe.gguf": b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile(path, 3, HASH), RepoFile("safe.gguf", 3, HASH))
    )
    assert state.files[path].state == "unavailable"
    assert state.files["safe.gguf"].state == "archived"


def test_malformed_source_url_cannot_crash_listing(
    tmp_path: Path, sample_record_dict: Any, write_model: Any
) -> None:
    malformed = artifact(file_entry("gguf/a.gguf"))
    malformed["source_repo"] = "https://["
    populate(tmp_path, sample_record_dict, write_model, malformed, payloads={"gguf/a.gguf": b"abc"})
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state in {"not-archived", "unavailable"}


@pytest.mark.parametrize("component", ["payload", "record"])
def test_directory_in_place_of_record_or_payload_is_unavailable(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, component: str
) -> None:
    directory = populate(
        tmp_path, sample_record_dict, write_model, artifact(file_entry("gguf/a.gguf"))
    )
    target = directory / ("gguf/a.gguf" if component == "payload" else "model-record.json")
    if target.exists():
        target.unlink()
    target.mkdir(parents=True)
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "unavailable"


def test_generated_artifact_cannot_stand_in_for_archived_original(
    tmp_path: Path, sample_record_dict: Any, write_model: Any
) -> None:
    generated = file_entry("gguf/a.gguf")
    generated["source"] = "generated"
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(generated),
        payloads={"gguf/a.gguf": b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "not-archived"


def test_sha256_comparison_is_case_insensitive(
    tmp_path: Path, sample_record_dict: Any, write_model: Any
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf")),
        payloads={"gguf/a.gguf": b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH.upper()))
    )
    assert state.files["a.gguf"].state == "archived"
    assert state.files["a.gguf"].comparison == "sha256"


def test_weight_under_document_namespace_remains_an_unambiguous_original(
    tmp_path: Path, sample_record_dict: Any, write_model: Any
) -> None:
    hub_path = "docs/acme--tiny-chat/model.gguf"
    target = f"gguf/{hub_path}"
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry(target)),
        payloads={target: b"abc"},
    )
    snapshot = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile(hub_path, 3, HASH))
    )
    assert snapshot.files[hub_path].state == "archived"
    assert snapshot.files[hub_path].comparison == "sha256"
