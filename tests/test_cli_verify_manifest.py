"""CLI tests for `llm-preserver verify` (spec 0009): sidecar and read-only.

The manifest-sha256.txt regeneration contract, the byte-identical
read-only guarantee, per-file I/O fault handling, and Ctrl-C. Report
text and exit codes live in test_cli_verify.py (300-line rule).
"""

import contextlib
import hashlib
import importlib
import re
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.pull_record import MANIFEST_FILENAME

runner = CliRunner()

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"
EVIL = b"evil payload bytes"  # same length as PAYLOAD: only the hash differs
DOC_REL = "gguf/docs/README.md"
DOC = b"# tiny-chat docs\n"
STALE_MANIFEST = "0" * 64 + "  stale-line.bin\n"


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


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


def init_archive_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def snapshot_files(root: Path) -> dict[Path, tuple[bytes, int]]:
    """Bytes and mtime of every non-manifest file under ``root``."""
    return {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST_FILENAME
    }


@pytest.fixture
def build_model(
    write_model: Callable[..., Path], sample_record_dict: Callable[..., dict]
) -> Callable[..., Path]:
    """Create a model dir with the given record entries and on-disk bytes."""

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


def test_full_verify_writes_sha256sum_compatible_manifest(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    assert not (model_dir / MANIFEST_FILENAME).exists()

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    lines = (model_dir / MANIFEST_FILENAME).read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        # `sha256sum -c` format: digest, exactly two spaces, path.
        assert re.fullmatch(r"[0-9a-f]{64}  \S.*", line), line
    assert f"{hex_of(PAYLOAD)}  {PAYLOAD_REL}" in lines
    assert any(line.endswith("  model-record.json") for line in lines)


def test_manifest_record_line_hashes_the_on_disk_record_bytes(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    from llm_preserver.records import load_record

    archive = init_archive_dir(tmp_path)
    # write_model serializes with json.dumps (compact) — deliberately
    # different bytes from a fresh model_dump_json(indent=2) round-trip,
    # so hashing a re-serialization instead of the disk bytes would
    # produce a manifest `sha256sum -c` immediately rejects.
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    disk_bytes = (model_dir / "model-record.json").read_bytes()
    fresh = (load_record(model_dir).model_dump_json(indent=2) + "\n").encode("utf-8")
    assert fresh != disk_bytes  # the discriminating setup still holds
    lines = (model_dir / MANIFEST_FILENAME).read_text(encoding="utf-8").splitlines()
    record_line = next(line for line in lines if line.endswith("  model-record.json"))
    assert record_line.split()[0] == hashlib.sha256(disk_bytes).hexdigest()


def test_full_verify_refreshes_manifest_for_a_drifted_model(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: EVIL})
    (model_dir / MANIFEST_FILENAME).write_text(STALE_MANIFEST, encoding="utf-8")

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5  # drifted, and still refreshed
    manifest = (model_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert manifest != STALE_MANIFEST
    # Derived from the record — the surviving truth — not from disk.
    assert f"{hex_of(PAYLOAD)}  {PAYLOAD_REL}" in manifest


def test_quick_writes_no_manifest(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})

    result = runner.invoke(app, ["verify", str(archive), "--quick"])

    assert result.exit_code == 0
    assert not (model_dir / MANIFEST_FILENAME).exists()


def test_quick_leaves_a_stale_manifest_untouched(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    (model_dir / MANIFEST_FILENAME).write_text(STALE_MANIFEST, encoding="utf-8")

    result = runner.invoke(app, ["verify", str(archive), "--quick"])

    assert result.exit_code == 0
    assert (model_dir / MANIFEST_FILENAME).read_text(encoding="utf-8") == STALE_MANIFEST


def test_verify_leaves_every_non_manifest_file_byte_identical(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD), entry_for(DOC_REL, DOC, sha256=None)],
        {PAYLOAD_REL: EVIL, DOC_REL: DOC},  # drifted on purpose
    )
    (model_dir / "gguf" / "leftover.bin").write_bytes(b"unrecorded extra")
    (model_dir / "MODEL-RECORD.md").write_text("# rendered\n", encoding="utf-8")
    before = snapshot_files(archive)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    assert snapshot_files(archive) == before


def test_unreadable_payload_is_a_per_file_error_not_a_crash(
    tmp_path: Path, build_model: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NAS share can be partially readable: report the file, keep going."""
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: PAYLOAD},
        creator="beta",
        model="coder",
    )
    hashing = importlib.import_module("llm_preserver.hashing")
    real = hashing.sha256_of
    unreadable = archive / "models" / "acme" / "tiny-chat" / PAYLOAD_REL

    def failing(path: Path) -> str:
        if Path(path) == unreadable:
            raise OSError("Input/output error")
        return real(path)

    monkeypatch.setattr(hashing, "sha256_of", failing)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    out = output_of(result)
    assert PAYLOAD_REL in out
    assert "Input/output error" in out
    # The run continued past the fault: the second model was still checked.
    assert "beta/coder" in out


def test_interrupt_mid_hash_exits_130_without_sidecar_debris(
    tmp_path: Path, build_model: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    (model_dir / MANIFEST_FILENAME).write_text(STALE_MANIFEST, encoding="utf-8")
    hashing = importlib.import_module("llm_preserver.hashing")

    def interrupted(path: Path) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(hashing, "sha256_of", interrupted)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 130
    # No partially written sidecar: the prior manifest survives intact
    # and no atomic-write temp file is left behind.
    assert (model_dir / MANIFEST_FILENAME).read_text(encoding="utf-8") == STALE_MANIFEST
    assert list(archive.rglob("*.tmp")) == []
