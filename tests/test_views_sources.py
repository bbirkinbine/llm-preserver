"""Source scan for runtime views (spec 0002 phase 1) — shared core.

``llm_preserver.views.scan_view_sources(archive_root)`` returns a scan
whose ``.models`` items carry ``model_id``, ``eligible`` (file objects
with ``.path`` absolute into the archive and ``.sha256`` verbatim from
the record) and ``skips`` (objects with a ``.reason`` string). The
scan is record-driven: payload bytes are never opened.
"""

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.views import scan_view_sources


def hexdigest(seed: str) -> str:
    """A deterministic, well-formed (lowercase 64-hex) fake digest."""
    return hashlib.sha256(seed.encode()).hexdigest()


def file_entry(rel_path: str, sha256: str | None, size: int = 0) -> dict[str, object]:
    return {"path": rel_path, "sha256": sha256, "size": size, "source": "original"}


def scan_for(archive: Path):
    return scan_view_sources(archive)


def by_id(scan) -> dict[str, object]:
    return {model.model_id: model for model in scan.models}


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    from llm_preserver.archive import init_archive

    root = tmp_path / "archive"
    init_archive(root)
    return root


@pytest.fixture
def build_model(
    write_model: Callable[..., Path], sample_record_dict: Callable[..., dict]
) -> Callable[..., Path]:
    """Model dir with the given file entries plus 0-byte payload stand-ins."""

    def _build(
        archive: Path,
        creator: str,
        model: str,
        entries: list[dict[str, object]],
        fmt: str = "gguf",
    ) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["format"] = fmt
        if fmt != "gguf":
            record["artifacts"][0]["quantization"] = None
        record["artifacts"][0]["files"] = entries
        model_dir = write_model(archive, record, creator=creator, model=model)
        for entry in entries:
            target = model_dir / str(entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
        return model_dir

    return _build


def test_hashed_gguf_files_are_eligible_with_verbatim_digests(
    archive: Path, build_model: Callable[..., Path]
) -> None:
    q4, q8 = hexdigest("q4"), hexdigest("q8")
    model_dir = build_model(
        archive,
        "acme",
        "tiny-chat",
        [
            file_entry("gguf/tiny-chat-Q4_K_M.gguf", q4),
            file_entry("gguf/tiny-chat-Q8_0.gguf", q8),
        ],
    )

    model = by_id(scan_for(archive))["acme/tiny-chat"]

    assert {f.sha256 for f in model.eligible} == {q4, q8}
    paths = {Path(f.path) for f in model.eligible}
    assert all(path.is_absolute() for path in paths)
    assert model_dir / "gguf/tiny-chat-Q4_K_M.gguf" in paths
    assert model_dir / "gguf/tiny-chat-Q8_0.gguf" in paths


def test_safetensors_only_model_is_skipped_with_reason(
    archive: Path, build_model: Callable[..., Path]
) -> None:
    build_model(
        archive,
        "acme",
        "snapshot-only",
        [file_entry("model.safetensors", hexdigest("st"))],
        fmt="hf-snapshot",
    )

    model = by_id(scan_for(archive))["acme/snapshot-only"]

    assert list(model.eligible) == []
    assert any("safetensors" in skip.reason.lower() for skip in model.skips)


def test_unhashed_gguf_is_skipped_while_hashed_sibling_stays_eligible(
    archive: Path, build_model: Callable[..., Path]
) -> None:
    q4 = hexdigest("q4")
    build_model(
        archive,
        "acme",
        "tiny-chat",
        [
            file_entry("gguf/tiny-chat-Q4_K_M.gguf", q4),
            file_entry("gguf/tiny-chat-Q8_0.gguf", None),
        ],
    )

    model = by_id(scan_for(archive))["acme/tiny-chat"]

    assert [f.sha256 for f in model.eligible] == [q4]
    assert any("unhashed" in skip.reason.lower() for skip in model.skips)


def test_sharded_gguf_set_is_skipped_with_reason(
    archive: Path, build_model: Callable[..., Path]
) -> None:
    build_model(
        archive,
        "acme",
        "big-chat",
        [
            file_entry("gguf/big-chat-Q4_K_M-00001-of-00002.gguf", hexdigest("s1")),
            file_entry("gguf/big-chat-Q4_K_M-00002-of-00002.gguf", hexdigest("s2")),
        ],
    )

    model = by_id(scan_for(archive))["acme/big-chat"]

    assert list(model.eligible) == []  # sharded GGUF is out of scope in phase 1
    assert any("shard" in skip.reason.lower() for skip in model.skips)


def test_scan_never_opens_payload_files(archive: Path, build_model: Callable[..., Path]) -> None:
    """Digests come from the record: an unreadable payload cannot break the scan."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    q4 = hexdigest("q4")
    rel = "gguf/tiny-chat-Q4_K_M.gguf"
    model_dir = build_model(archive, "acme", "tiny-chat", [file_entry(rel, q4)])
    payload = model_dir / rel
    payload.chmod(0o000)
    try:
        model = by_id(scan_for(archive))["acme/tiny-chat"]
    finally:
        payload.chmod(0o644)

    assert [f.sha256 for f in model.eligible] == [q4]


def test_malformed_record_digest_never_becomes_eligible(
    archive: Path,
    write_model: Callable[..., Path],
    sample_record_dict: Callable[..., dict],
) -> None:
    """A corrupted digest must not survive into a ``sha256-<digest>`` filename."""
    record = sample_record_dict(name="evil", hub_id="acme/evil")
    record["artifacts"][0]["files"] = [
        {
            "path": "gguf/evil.gguf",
            "sha256": "../../../escape-attempt",  # not 64-hex: corrupted record
            "size": 0,
            "source": "original",
        }
    ]
    write_model(archive, record, creator="acme", model="evil")

    scan = scan_for(archive)  # must not raise

    assert [f.sha256 for model in scan.models for f in model.eligible] == []


def test_symlinked_payload_is_skipped_never_linked(
    archive: Path, build_model: Callable[..., Path], tmp_path: Path
) -> None:
    """0009/0010 posture: a recorded file that is a symlink is refused."""
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"")
    rel = "gguf/tiny-chat-Q4_K_M.gguf"
    model_dir = build_model(archive, "acme", "tiny-chat", [file_entry(rel, hexdigest("q4"))])
    payload = model_dir / rel
    payload.unlink()
    payload.symlink_to(outside)

    model = by_id(scan_for(archive))["acme/tiny-chat"]

    assert list(model.eligible) == []
    assert any("symlink" in skip.reason.lower() for skip in model.skips)


def test_path_through_symlinked_dir_escaping_archive_is_skipped(
    archive: Path, build_model: Callable[..., Path], tmp_path: Path
) -> None:
    """A symlinked intermediate dir must not smuggle an outside target."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "tiny-chat-Q4_K_M.gguf").write_bytes(b"")
    rel = "gguf/tiny-chat-Q4_K_M.gguf"
    model_dir = build_model(archive, "acme", "tiny-chat", [file_entry(rel, hexdigest("q4"))])
    gguf_dir = model_dir / "gguf"
    (gguf_dir / "tiny-chat-Q4_K_M.gguf").unlink()
    gguf_dir.rmdir()
    gguf_dir.symlink_to(outside_dir, target_is_directory=True)

    model = by_id(scan_for(archive))["acme/tiny-chat"]

    assert list(model.eligible) == []
    assert model.skips  # refused with a reason, not silently dropped


def test_invalid_directory_name_is_skipped_whole(
    archive: Path, build_model: Callable[..., Path]
) -> None:
    """A hostile dir name must never reach a minted name (0007 posture)."""
    build_model(archive, "acme;evil", "tiny-chat", [file_entry("gguf/x.gguf", hexdigest("q4"))])

    model = by_id(scan_for(archive))["acme;evil/tiny-chat"]

    assert list(model.eligible) == []
    assert any("not a valid" in skip.reason for skip in model.skips)


def test_non_gguf_artifact_skips_roll_up_per_artifact(
    archive: Path, build_model: Callable[..., Path]
) -> None:
    """32 snapshot files produce one reasoned line, not 32."""
    entries = [file_entry(f"shard-{i:05d}.safetensors", hexdigest(f"s{i}")) for i in range(32)]
    build_model(archive, "acme", "snapshot-only", entries, fmt="hf-snapshot")

    model = by_id(scan_for(archive))["acme/snapshot-only"]

    assert len(model.skips) == 1
    assert "32 files" in model.skips[0].path
