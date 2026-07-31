"""Tests for llm_preserver.cli pull — Ollama-shape hint on invalid ids.

Spec 0013 phase B, CLI end to end. The 0011 clean invalid-id error
gains shape detection: an Ollama ``name:tag`` id adds a hint naming
``discover`` / ``discover --match-ollama``; an ``hf.co/<org>/<repo>``
id adds the exact mechanical ``pull`` translation. Exit codes and the
0011 message itself are unchanged, and non-Ollama-shaped invalid ids
get no hint.

Like test_cli_pull_errors.py's invalid-id test, the shape-detection
tests drive the REAL hub client on purpose: the hint rides the
``HFValidationError -> PullUserError`` mapping inside the client's
``except MAPPED_EXCEPTIONS`` seam, which a FakeHubClient bypasses. The
library's ``validate_repo_id`` rejects each id locally, before any HTTP
request, so no network is touched; HF_HUB_OFFLINE is set as a belt.
Split from test_cli_pull_errors.py (300-line rule).
"""

import contextlib

import click
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    """Swap the CLI's hub-client seam for a fake (see test_cli_pull.py)."""
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke_pull_invalid_id(monkeypatch, tmp_path, raw_id):
    """Run pull with an id the real client rejects locally (0011 pattern)."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    archive = init_archive_dir(tmp_path)
    result = runner.invoke(app, ["pull", raw_id, str(archive), "--include", "*", "--yes"])
    return result, click.unstyle(combined_output(result))


def test_name_tag_id_keeps_the_0011_error_and_exit_code(tmp_path, monkeypatch):
    result, output = invoke_pull_invalid_id(monkeypatch, tmp_path, "qwen3-vl:30b-a3b-instruct")

    assert result.exit_code == 2, output
    assert "not a valid Hugging Face repo id" in output
    assert "Traceback (most recent call last)" not in output


def test_name_tag_id_adds_the_ollama_shape_hint(tmp_path, monkeypatch):
    result, output = invoke_pull_invalid_id(monkeypatch, tmp_path, "qwen3-vl:30b-a3b-instruct")

    assert result.exit_code == 2, output
    assert "Ollama" in output
    assert "--match-ollama" in output


def test_hf_co_id_adds_the_mechanical_pull_translation(tmp_path, monkeypatch):
    result, output = invoke_pull_invalid_id(
        monkeypatch, tmp_path, "hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M"
    )

    assert result.exit_code == 2, output
    assert "pull unsloth/Qwen3-8B-GGUF" in output
    assert "--include" in output
    assert "Q4_K_M" in output


def test_non_ollama_shaped_invalid_id_gets_no_hint(tmp_path, monkeypatch):
    # Too many slashes, no colon, no hf.co prefix: trips HFValidationError
    # (verified against huggingface_hub's validate_repo_id) but is not
    # Ollama-shaped, so the 0011 message stands alone.
    result, output = invoke_pull_invalid_id(monkeypatch, tmp_path, "a/b/c")

    assert result.exit_code == 2, output
    assert "not a valid Hugging Face repo id" in output
    assert "--match-ollama" not in output


def test_valid_repo_id_pull_never_prints_the_hint(tmp_path, monkeypatch, fake_hub_factory):
    # Happy path via the fake hub (test_cli_pull.py pattern): a valid id
    # pulls normally and the shape hint never fires.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        [
            "pull",
            "bartowski/tiny-chat-GGUF",
            str(archive),
            "--include",
            "*Q4_K_M*",
            "--model",
            "acme/tiny-chat",
            "--yes",
        ],
    )

    output = click.unstyle(combined_output(result))
    assert result.exit_code == 0, output
    assert "--match-ollama" not in output
