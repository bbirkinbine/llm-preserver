"""CLI tests for `llm-preserver verify` (spec 0009): review-round pins.

Hostile-archive regressions from the security review (symlinked
payloads, a planted sidecar-tmp symlink) and edge coverage from the
standard review (--model + --quick composition, empty archive,
uppercase recorded digests, out-of-scope sidecars). The main behavior
matrix lives in test_cli_verify.py / test_cli_verify_manifest.py.
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


@pytest.fixture
def hash_calls(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every path hashed through the ``llm_preserver.hashing`` seam."""
    hashing = importlib.import_module("llm_preserver.hashing")
    calls: list[Path] = []
    real = hashing.sha256_of

    def counting(path: Path) -> str:
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(hashing, "sha256_of", counting)
    return calls


# --- hostile archives (security-review regressions) ---


def test_symlinked_payload_is_refused_not_hashed(
    tmp_path: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    """A recorded path that is a symlink is drift, never followed.

    The link target's content matches the recorded hash on purpose: a
    verify that followed the link would report the model valid.
    """
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {})
    outside = tmp_path / "outside.bin"
    outside.write_bytes(PAYLOAD)
    (model_dir / PAYLOAD_REL).parent.mkdir(parents=True, exist_ok=True)
    (model_dir / PAYLOAD_REL).symlink_to(outside)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    out = output_of(result)
    assert re.search(r"\binvalid\b", out)
    assert "symlink" in out
    assert hash_calls == []  # the out-of-tree file was never read


def test_symlinked_intermediate_directory_is_refused(
    tmp_path: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    """A recorded path crossing a symlinked directory never escapes."""
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {})
    outside_dir = tmp_path / "outside-tree"
    outside_dir.mkdir()
    (outside_dir / Path(PAYLOAD_REL).name).write_bytes(PAYLOAD)
    (model_dir / "gguf").symlink_to(outside_dir, target_is_directory=True)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    assert "symlink" in output_of(result)
    assert hash_calls == []


def test_planted_manifest_tmp_symlink_cannot_redirect_the_write(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """A pre-planted sidecar-tmp symlink must not clobber its target."""
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    victim = tmp_path / "victim.txt"
    victim_bytes = b"must survive verify untouched"
    victim.write_bytes(victim_bytes)
    (model_dir / (MANIFEST_FILENAME + ".tmp")).symlink_to(victim)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    assert victim.read_bytes() == victim_bytes
    manifest = (model_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert f"{hex_of(PAYLOAD)}  {PAYLOAD_REL}" in manifest


# --- edges from the standard review ---


def test_model_scope_and_quick_compose(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: PAYLOAD},
        creator="beta",
        model="coder",
    )

    result = runner.invoke(app, ["verify", str(archive), "--model", "acme/tiny-chat", "--quick"])

    assert result.exit_code == 0
    out = output_of(result)
    assert re.search(r"\bcomplete\b", out)
    assert "beta/coder" not in out
    # Quick writes no sidecar anywhere, scoped or not.
    assert list(archive.rglob(MANIFEST_FILENAME)) == []


def test_quick_with_unknown_model_still_exits_two(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})

    result = runner.invoke(app, ["verify", str(archive), "--model", "acme/no-such", "--quick"])

    assert result.exit_code == 2
    assert "acme/tiny-chat" in output_of(result)


def test_empty_archive_reports_and_exits_zero(tmp_path: Path) -> None:
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    assert "archive is empty" in output_of(result)


def test_uppercase_recorded_hash_still_verifies_valid(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """The record schema accepts uppercase hex; comparison must not care."""
    archive = init_archive_dir(tmp_path)
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD, sha256=hex_of(PAYLOAD).upper())],
        {PAYLOAD_REL: PAYLOAD},
    )

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    assert re.search(r"\bvalid\b", output_of(result))


def test_read_only_model_dir_warns_and_the_audit_continues(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """An unwritable sidecar is a warning, never a crash or drift.

    A deliberately read-only-mounted archive is a legitimate
    preservation posture; its payloads still verify (adjudicated
    2026-07-13).
    """
    archive = init_archive_dir(tmp_path)
    locked_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: PAYLOAD},
        creator="beta",
        model="coder",
    )
    locked_dir.chmod(0o555)
    try:
        result = runner.invoke(app, ["verify", str(archive)])
    finally:
        locked_dir.chmod(0o755)

    assert result.exit_code == 0  # payloads verified fine; no drift
    out = output_of(result)
    assert "manifest not refreshed" in out
    assert "beta/coder" in out  # the audit continued past the fault


def test_all_unhashed_model_reports_complete_not_valid_on_a_full_run(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """A record with no hashes was never validated, only found complete."""
    archive = init_archive_dir(tmp_path)
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD, sha256=None)],
        {PAYLOAD_REL: PAYLOAD},
    )

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    out = output_of(result)
    assert re.search(r"\bcomplete\b", out)
    assert re.search(r"\bvalid\b", out) is None


def test_scoped_full_run_never_writes_out_of_scope_sidecar(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """--model must not touch models it did not name — sidecars included."""
    archive = init_archive_dir(tmp_path)
    scoped_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    other_dir = build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: PAYLOAD},
        creator="beta",
        model="coder",
    )

    result = runner.invoke(app, ["verify", str(archive), "--model", "acme/tiny-chat"])

    assert result.exit_code == 0
    assert (scoped_dir / MANIFEST_FILENAME).is_file()
    assert not (other_dir / MANIFEST_FILENAME).exists()
