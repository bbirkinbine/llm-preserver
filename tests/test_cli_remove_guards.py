"""CLI tests for `llm-preserver remove` (spec 0010): guards and faults.

Exit-code domains (0009 stance: exit codes carry the scripting
contract), the non-TTY refusal, the symlink defense, the Ctrl-C re-run
command (0007 precedent), and the TTY-gated per-file progress on
stderr (0009 live-use lesson). Happy paths live in test_cli_remove.py.

Expected red (test-first): the `remove` command is not registered yet.
"""

import contextlib
import hashlib
import os
import shlex
from collections.abc import Callable
from pathlib import Path

import click
import pytest
import typer.testing
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.records import RECORD_FILENAME

runner = CliRunner()

MODEL_ID = "acme/tiny-chat"
Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"
Q4 = b"q4 weight bytes"
Q8_REL = "gguf/tiny-chat-Q8_0.gguf"
Q8 = b"q8 weight bytes"


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


def stderr_of(result) -> str:
    """Unstyled stderr alone; empty when the runner did not separate it."""
    with contextlib.suppress(ValueError, AttributeError):
        return click.unstyle(result.stderr)
    return ""


def entry_for(rel_path: str, content: bytes) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    return {
        "path": rel_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "source": "original",
    }


def init_archive_dir(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    archive = base / "archive"
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


def two_quant_model(build_model: Callable[..., Path], archive: Path) -> Path:
    return build_model(
        archive, [entry_for(Q4_REL, Q4), entry_for(Q8_REL, Q8)], {Q4_REL: Q4, Q8_REL: Q8}
    )


def test_malformed_model_id_exits_one(tmp_path: Path) -> None:
    """Same strict <creator>/<model> validation as show, before any path."""
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["remove", "noslash", str(archive), "--yes"])

    assert result.exit_code == 1
    assert "creator" in output_of(result)


def test_unknown_model_exits_two_and_lists_archived_ids(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """The verify --model self-correction style (spec 0009 precedent)."""
    archive = init_archive_dir(tmp_path)
    two_quant_model(build_model, archive)
    build_model(archive, [entry_for(Q4_REL, Q4)], {Q4_REL: Q4}, creator="beta", model="coder")

    result = runner.invoke(app, ["remove", "acme/no-such-model", str(archive), "--yes"])

    assert result.exit_code == 2
    out = output_of(result)
    assert "acme/tiny-chat" in out
    assert "beta/coder" in out


def test_pattern_matching_nothing_exits_two_echoing_the_pattern(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """A no-op delete request is user error, not success (spec 0010)."""
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)

    result = runner.invoke(
        app, ["remove", MODEL_ID, str(archive), "--include", "*Q9_NOPE*", "--yes"]
    )

    assert result.exit_code == 2
    assert "*Q9_NOPE*" in output_of(result)
    assert (model_dir / Q4_REL).exists()
    assert (model_dir / Q8_REL).exists()


def test_non_tty_without_yes_refuses_on_exit_two(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """No hanging prompt in a pipe: refuse deterministically, name the
    bypass (pull's unanswerable-confirmation precedent)."""
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)

    result = runner.invoke(app, ["remove", MODEL_ID, str(archive)])  # no stdin, no --yes

    assert result.exit_code == 2
    assert "--yes" in output_of(result)
    assert (model_dir / RECORD_FILENAME).is_file()
    assert (model_dir / Q4_REL).exists()


def test_piped_yes_without_the_flag_still_refuses(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """Tightened guard (spec 0010): a non-interactive run means --yes,
    period. A piped 'y' must not stand in for a human on the tool's one
    irreversible operation — refuse (exit 2), delete nothing."""
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)

    result = runner.invoke(app, ["remove", MODEL_ID, str(archive)], input="y\n")

    assert result.exit_code == 2
    assert "--yes" in output_of(result)
    assert (model_dir / RECORD_FILENAME).is_file()  # nothing deleted
    assert (model_dir / Q4_REL).exists()


def test_symlinked_model_dir_is_refused(tmp_path: Path) -> None:
    """Remove must never follow a link out of the archive root."""
    archive = init_archive_dir(tmp_path)
    outside = tmp_path / "outside-tree"
    (outside / "gguf").mkdir(parents=True)
    victim = outside / "gguf" / "victim.gguf"
    victim.write_bytes(b"must survive")
    (archive / "models" / "acme").mkdir(parents=True)
    (archive / "models" / "acme" / "tiny-chat").symlink_to(outside, target_is_directory=True)

    result = runner.invoke(app, ["remove", MODEL_ID, str(archive), "--yes"])

    assert result.exit_code == 1
    assert "symlink" in output_of(result)
    assert victim.read_bytes() == b"must survive"


def test_ctrl_c_mid_deletion_exits_130_and_prints_the_rerun_command(
    tmp_path: Path, build_model: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """0007 precedent: no resume state — re-running the printed command
    finishes the job, so the interrupt's final line must be paste-ready
    (absolute archive path, quoted patterns) and must NOT carry --yes:
    the re-run gets its own preview and confirmation."""
    archive = init_archive_dir(tmp_path)
    two_quant_model(build_model, archive)

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "unlink", interrupt)
    monkeypatch.setattr(os, "remove", interrupt)

    result = runner.invoke(
        app, ["remove", MODEL_ID, str(archive), "--include", "*Q4_K_M*", "--yes"]
    )

    assert result.exit_code == 130
    final_line = click.unstyle(result.stdout).rstrip().splitlines()[-1]
    assert "remove" in final_line
    assert MODEL_ID in final_line
    assert str(archive.resolve()) in final_line
    assert f"--include {shlex.quote('*Q4_K_M*')}" in final_line
    assert "--yes" not in final_line


def test_ctrl_c_at_confirmation_prompt_declines_and_exits_zero(
    tmp_path: Path, build_model: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C at an interactive prompt is a decline, not a fault (spec
    0010): nothing removed, exit 0 — the same as a typed 'n'. confirm()
    raises Abort for both Ctrl-C and pipe-EOF; isatty tells them apart.
    """
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)

    def abort(*args: object, **kwargs: object) -> bool:
        raise typer.Abort

    # An attended terminal (isatty True) where confirm is interrupted.
    monkeypatch.setattr(typer.testing._NamedTextIOWrapper, "isatty", lambda self: True)
    monkeypatch.setattr(typer, "confirm", abort)

    result = runner.invoke(app, ["remove", MODEL_ID, str(archive)])

    assert result.exit_code == 0
    assert "nothing removed" in output_of(result)
    assert (model_dir / RECORD_FILENAME).is_file()  # nothing deleted
    assert (model_dir / Q4_REL).exists()


def test_readonly_model_dir_fails_cleanly_not_with_a_traceback(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """A pattern removal that cannot write the record (read-only mount)
    exits 1 with a specific message, not a bare traceback (spec 0009
    hardened verify the same way); the record stays intact."""
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)
    model_dir.chmod(0o500)
    try:
        result = runner.invoke(
            app, ["remove", MODEL_ID, str(archive), "--include", "*Q4*", "--yes"]
        )
    finally:
        model_dir.chmod(0o700)

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert (model_dir / Q4_REL).exists()  # nothing deleted


def test_tty_progress_goes_to_stderr_and_stdout_stays_identical(
    tmp_path: Path, build_model: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-file lines gate on stderr.isatty() (spec 0010, the 0009
    live-use lesson); a piped run's stdout stays byte-identical so
    script logs never change shape."""
    plain_archive = init_archive_dir(tmp_path / "plain")
    two_quant_model(build_model, plain_archive)
    tty_archive = init_archive_dir(tmp_path / "tty")
    two_quant_model(build_model, tty_archive)

    plain = runner.invoke(app, ["remove", MODEL_ID, str(plain_archive), "--yes"])
    with monkeypatch.context() as mp:
        # Force isatty on the runner's replacement streams — the only
        # seam CliRunner leaves for simulating an attended terminal.
        # typer.testing bundles its own click, so its runner streams are
        # typer.testing._NamedTextIOWrapper, not click.testing's.
        mp.setattr(typer.testing._NamedTextIOWrapper, "isatty", lambda self: True)
        tty = runner.invoke(app, ["remove", MODEL_ID, str(tty_archive), "--yes"])

    assert plain.exit_code == 0
    assert tty.exit_code == 0
    # Per-file progress named on stderr only when it is a terminal.
    assert "tiny-chat-Q4_K_M.gguf" in stderr_of(tty)
    assert "tiny-chat-Q4_K_M.gguf" not in stderr_of(plain)
    # stdout identical once the differing archive paths are normalized.
    plain_stdout = click.unstyle(plain.stdout).replace(str(plain_archive.resolve()), "<A>")
    plain_stdout = plain_stdout.replace(str(plain_archive), "<A>")
    tty_stdout = click.unstyle(tty.stdout).replace(str(tty_archive.resolve()), "<A>")
    tty_stdout = tty_stdout.replace(str(tty_archive), "<A>")
    assert plain_stdout == tty_stdout
