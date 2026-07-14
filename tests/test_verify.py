"""Tests for the spec-0009 core: llm_preserver.verify and llm_preserver.hashing.

The CLI-free audit surface: ``verify_archive`` walks an archive root
against its records (check order existence -> size -> hash, fail-fast)
and ``hashing.sha256_of`` is the streaming hash extracted from
``pull_plan``. Neither module exists yet (test-first): the imports
happen inside test bodies and fixtures so collection of the rest of
the suite never depends on them — the expected red state here is
ModuleNotFoundError, per test. CLI behavior (report text, exit codes)
lives in test_cli_verify*.py.
"""

import hashlib
import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"
EVIL = b"evil payload bytes"  # same length as PAYLOAD: only the hash differs
DOC_REL = "gguf/docs/README.md"
DOC = b"# tiny-chat docs\n"


def hex_of(content: bytes) -> str:
    """SHA256 hex digest of ``content``."""
    return hashlib.sha256(content).hexdigest()


def entry_for(rel_path: str, content: bytes, **overrides: object) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    made: dict[str, object] = {
        "path": rel_path,
        "sha256": hex_of(content),
        "size": len(content),
        "source": "original",
    }
    made.update(overrides)
    return made


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    """An initialized (empty) archive under tmp_path."""
    from llm_preserver.archive import init_archive

    root = tmp_path / "archive"
    init_archive(root)
    return root


@pytest.fixture
def build_model(
    write_model: Callable[..., Path], sample_record_dict: Callable[..., dict]
) -> Callable[..., Path]:
    """Create a model dir with the given record entries and on-disk bytes.

    ``entries`` become the record's file list; ``payloads`` maps
    model-dir-relative paths to the bytes written on disk — omit a
    recorded path to simulate a missing file, write different bytes to
    simulate drift.
    """

    def _build(
        archive: Path,
        entries: list[dict[str, object]],
        payloads: dict[str, bytes],
        creator: str = "acme",
        model: str = "tiny-chat",
    ) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["files"] = entries
        model_dir = write_model(archive, record, creator=creator, model=model)
        for rel_path, content in payloads.items():
            target = model_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return model_dir

    return _build


@pytest.fixture
def hash_calls(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every path hashed through the ``llm_preserver.hashing`` seam.

    Contract this pins: verify resolves the hash function as a module
    attribute at call time (``hashing.sha256_of(...)``), not an
    early ``from``-import binding, so tests can observe — and inject
    faults into — every hash call.
    """
    hashing = importlib.import_module("llm_preserver.hashing")
    calls: list[Path] = []
    real = hashing.sha256_of

    def counting(path: Path) -> str:
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(hashing, "sha256_of", counting)
    return calls


def one_result(archive_root: Path, **kwargs: object) -> Any:  # Any: surface under test-first
    """Run verify_archive and return the single per-model result."""
    from llm_preserver.verify import verify_archive

    results = verify_archive(archive_root, **kwargs).models
    assert len(results) == 1
    return results[0]


# --- hashing.sha256_of (extracted from pull_plan) ---


def test_sha256_of_matches_hashlib_for_a_small_file(tmp_path: Path) -> None:
    from llm_preserver.hashing import sha256_of

    target = tmp_path / "blob.bin"
    target.write_bytes(PAYLOAD)

    assert sha256_of(target) == hashlib.sha256(PAYLOAD).hexdigest()


def test_sha256_of_streams_across_chunk_boundaries(tmp_path: Path) -> None:
    from llm_preserver.hashing import sha256_of

    content = b"x" * ((1 << 20) * 2 + 3)  # spans multiple 1 MiB read chunks
    target = tmp_path / "big.bin"
    target.write_bytes(content)

    assert sha256_of(target) == hashlib.sha256(content).hexdigest()


# --- verify_archive: per-model states ---


def test_intact_model_reports_state_valid(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})

    result = one_result(archive_root)

    assert result.model_id == "acme/tiny-chat"
    assert result.state == "valid"


def test_missing_recorded_file_reports_state_incomplete(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(
        archive_root,
        [entry_for(PAYLOAD_REL, PAYLOAD), entry_for(DOC_REL, DOC)],
        {PAYLOAD_REL: PAYLOAD},  # DOC_REL is recorded but never written
    )

    result = one_result(archive_root)

    assert result.state == "incomplete"


def test_hash_mismatch_reports_state_invalid(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: EVIL})

    result = one_result(archive_root)

    assert result.state == "invalid"


def test_size_mismatch_reports_invalid_without_hashing(
    archive_root: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    # The on-disk content hashes to the recorded sha256; only the
    # recorded size is wrong. A verify that hashed first would call
    # this file clean — the cheap-check order (size before hash) is
    # what makes it invalid, and fail-fast means no hash call at all.
    build_model(
        archive_root,
        [entry_for(PAYLOAD_REL, PAYLOAD, size=len(PAYLOAD) + 7)],
        {PAYLOAD_REL: PAYLOAD},
    )

    result = one_result(archive_root)

    assert result.state == "invalid"
    assert hash_calls == []


def test_missing_file_never_attempts_a_hash(
    archive_root: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD)], {})

    result = one_result(archive_root)

    assert result.state == "incomplete"
    assert hash_calls == []


# --- unhashed / unrecorded categories ---


def test_null_hash_entry_is_listed_as_unhashed_not_a_mismatch(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(
        archive_root,
        [entry_for(PAYLOAD_REL, PAYLOAD), entry_for(DOC_REL, DOC, sha256=None)],
        {PAYLOAD_REL: PAYLOAD, DOC_REL: DOC},
    )

    result = one_result(archive_root)

    assert result.unhashed == [DOC_REL]
    assert result.state != "invalid"


def test_null_size_entry_with_hash_is_still_hashed(
    archive_root: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD, size=None)], {PAYLOAD_REL: PAYLOAD})

    result = one_result(archive_root)

    assert result.state == "valid"
    assert [path.name for path in hash_calls] == [Path(PAYLOAD_REL).name]


def test_extra_on_disk_file_is_listed_as_unrecorded(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    model_dir = build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    (model_dir / "gguf" / "leftover.bin").write_bytes(b"dropped by hand")

    result = one_result(archive_root)

    assert result.unrecorded == ["gguf/leftover.bin"]
    assert result.state == "valid"  # unrecorded extras are informational, not drift


def test_tool_generated_files_are_exempt_from_unrecorded(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    model_dir = build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    # model-record.json already exists; the other two tool-owned files:
    (model_dir / "MODEL-RECORD.md").write_text("# rendered\n", encoding="utf-8")
    (model_dir / "manifest-sha256.txt").write_text("stale\n", encoding="utf-8")

    result = one_result(archive_root)

    assert result.unrecorded == []


# --- degraded models (status-style walk states) ---


def test_recordless_model_reports_no_record_state(
    archive_root: Path, write_model: Callable[..., Path]
) -> None:
    write_model(archive_root, record=None)

    result = one_result(archive_root)

    assert result.model_id == "acme/tiny-chat"
    assert result.state == "no-record"


def test_unparseable_record_reports_record_unreadable_state(
    archive_root: Path, write_model: Callable[..., Path]
) -> None:
    model_dir = write_model(archive_root, record=None)
    (model_dir / "model-record.json").write_text("{ this is not json", encoding="utf-8")

    result = one_result(archive_root)

    assert result.state == "record-unreadable"


# --- scoping and --quick ---


def test_model_scope_checks_only_the_named_model(
    archive_root: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    from llm_preserver.verify import verify_archive

    build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    build_model(
        archive_root,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: EVIL},  # drifted — must never be looked at
        creator="beta",
        model="coder",
    )

    results = verify_archive(archive_root, model="acme/tiny-chat").models

    assert [result.model_id for result in results] == ["acme/tiny-chat"]
    scoped_dir = archive_root / "models" / "acme" / "tiny-chat"
    assert hash_calls, "the scoped model's payload should have been hashed"
    assert all(scoped_dir in path.parents for path in hash_calls)


def test_quick_reports_complete_and_never_hashes(
    archive_root: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    from llm_preserver.verify import verify_archive

    build_model(archive_root, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})

    results = verify_archive(archive_root, quick=True).models

    assert results[0].state == "complete"
    assert hash_calls == []
