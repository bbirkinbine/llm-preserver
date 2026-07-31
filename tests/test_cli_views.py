"""CLI tests for the ``views`` command (spec 0002 phase 1).

Drives the real Typer app through CliRunner. The exit-2 tests assert
on message content as well as the code — a missing command must not
read as a passing usage-error test.

Output is unstyled (``click.unstyle``) before substring asserts
(rich-ANSI-in-CI rule). Exit-code contract follows the house style:
0 success, 1 archive/environment (``fail``) or nothing eligible,
2 user-input domain.
"""

import contextlib
import hashlib
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"
Q4_DIGEST = hashlib.sha256(b"q4").hexdigest()


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    return click.unstyle(combined_output(result))


def stderr_of(result) -> str:
    """Unstyled stderr; falls back to combined output on older click."""
    try:
        return click.unstyle(result.stderr)
    except (ValueError, AttributeError):
        return output_of(result)


def init_archive_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def file_entry(rel_path: str, sha256: str | None, size: int = 0) -> dict[str, object]:
    return {"path": rel_path, "sha256": sha256, "size": size, "source": "original"}


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


def gguf_archive(tmp_path: Path, build_model: Callable[..., Path]) -> Path:
    archive = init_archive_dir(tmp_path)
    build_model(archive, "acme", "tiny-chat", [file_entry(Q4_REL, Q4_DIGEST)])
    return archive


def views_cmd(archive: Path, dest: Path, *extra: str) -> list[str]:
    return ["views", str(archive), "--tool", "ollama", "--dest", str(dest), *extra]


def test_seed_run_builds_store_and_exits_zero(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = gguf_archive(tmp_path, build_model)
    dest = tmp_path / "view"

    result = runner.invoke(app, views_cmd(archive, dest, "--seed-store"))

    assert result.exit_code == 0
    assert (dest / "blobs" / f"sha256-{Q4_DIGEST}").is_symlink()


def test_seed_run_prints_breakdown_line(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    archive = gguf_archive(tmp_path, build_model)
    build_model(
        archive,
        "acme",
        "snapshot-only",
        [file_entry("model.safetensors", hashlib.sha256(b"st").hexdigest())],
        fmt="hf-snapshot",
    )

    result = runner.invoke(app, views_cmd(archive, tmp_path / "view", "--seed-store"))

    assert result.exit_code == 0
    out = output_of(result).lower()
    for word in ("scanned", "eligible", "skipped"):
        assert word in out
    assert "safetensors" in out  # a reason per skip, not just totals


def test_seed_run_prints_loud_warning_and_serve_instructions(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = gguf_archive(tmp_path, build_model)

    result = runner.invoke(app, views_cmd(archive, tmp_path / "view", "--seed-store"))

    assert result.exit_code == 0
    out = output_of(result)
    assert "not an officially supported" in out.lower()  # works, but not official
    assert "ollama list" in out  # registered directly; no create step
    assert "OLLAMA_MODELS" in out
    assert "OLLAMA_NOPRUNE=1" in out


def test_default_run_prints_instructions_and_writes_nothing(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = gguf_archive(tmp_path, build_model)
    dest = tmp_path / "view"

    result = runner.invoke(app, views_cmd(archive, dest))

    assert result.exit_code == 0
    out = output_of(result)
    for needle in ("ollama create", "OLLAMA_MODELS", "OLLAMA_NOPRUNE=1"):
        assert needle in out
    assert not dest.exists()  # default mode is instructions-only


def test_zero_eligible_exits_one_message_on_stderr_dest_untouched(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(
        archive,
        "acme",
        "snapshot-only",
        [file_entry("model.safetensors", hashlib.sha256(b"st").hexdigest())],
        fmt="hf-snapshot",
    )
    dest = tmp_path / "view"

    result = runner.invoke(app, views_cmd(archive, dest, "--seed-store"))

    assert result.exit_code == 1
    assert "eligible" in stderr_of(result).lower()
    assert not dest.exists()


def test_unknown_tool_value_exits_two(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    archive = gguf_archive(tmp_path, build_model)

    result = runner.invoke(
        app,
        ["views", str(archive), "--tool", "lmstudio-9000", "--dest", str(tmp_path / "view")],
    )

    assert result.exit_code == 2
    out = output_of(result)
    assert "no such command" not in out.lower()  # the command itself must exist
    assert "lmstudio-9000" in out  # the rejected value is named


def test_dest_inside_archive_exits_two(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    archive = gguf_archive(tmp_path, build_model)
    dest = archive / "view"

    result = runner.invoke(app, views_cmd(archive, dest, "--seed-store"))

    assert result.exit_code == 2
    out = output_of(result).lower()
    assert "no such command" not in out
    assert "archive" in out  # names the refusal, not a generic usage error
    assert not dest.exists()


def test_zero_eligible_default_mode_also_exits_one(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """The exit-1 contract is unconditional, not seed-mode-only."""
    archive = init_archive_dir(tmp_path)
    build_model(
        archive,
        "acme",
        "snapshot-only",
        [file_entry("model.safetensors", hashlib.sha256(b"st").hexdigest())],
        fmt="hf-snapshot",
    )

    result = runner.invoke(app, views_cmd(archive, tmp_path / "view"))

    assert result.exit_code == 1
    assert "eligible" in stderr_of(result).lower()


def test_degraded_dest_is_a_clean_exit_two_not_a_traceback(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """The 0011/0012 regression class, exercised through the CLI."""
    archive = gguf_archive(tmp_path, build_model)
    dest = tmp_path / "view"
    result = runner.invoke(app, views_cmd(archive, dest, "--seed-store"))
    assert result.exit_code == 0
    blobs = dest / "blobs"
    for entry in blobs.iterdir():
        entry.unlink()
    blobs.rmdir()
    blobs.write_text("not a dir", encoding="utf-8")

    result = runner.invoke(app, views_cmd(archive, dest, "--seed-store"))

    assert result.exit_code == 2
    out = output_of(result)
    assert "Traceback" not in out
    assert "refusing" in out.lower()


def test_uninitialized_archive_exits_one(tmp_path: Path) -> None:
    bare = tmp_path / "notarchive"
    bare.mkdir()

    result = runner.invoke(app, views_cmd(bare, tmp_path / "view", "--seed-store"))

    assert result.exit_code == 1  # house code: same domain as verify on a bare dir
    assert "archive" in output_of(result).lower()


def test_h_help_lists_tool_and_dest_options() -> None:
    result = runner.invoke(app, ["views", "-h"])

    assert result.exit_code == 0
    out = output_of(result)
    assert "--tool" in out
    assert "--dest" in out
