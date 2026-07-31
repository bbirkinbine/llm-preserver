"""The ``--dest`` env fallback for ``views`` (spec 0002).

``$LLM_PRESERVER_VIEWS`` is a views *root*: dest defaults to
``$LLM_PRESERVER_VIEWS/<tool>`` so later adapters get their own
subdirectory. An explicit ``--dest`` always wins. Mirrors the archive
path's ``$LLM_PRESERVER_ARCHIVE`` convention.
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

Q4_DIGEST = hashlib.sha256(b"q4").hexdigest()


def output_of(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return click.unstyle(out)


@pytest.fixture
def gguf_archive(
    tmp_path: Path,
    write_model: Callable[..., Path],
    sample_record_dict: Callable[..., dict],
) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    assert runner.invoke(app, ["init", str(archive)]).exit_code == 0
    record = sample_record_dict(name="tiny-chat", hub_id="acme/tiny-chat")
    record["artifacts"][0]["files"] = [
        {"path": "gguf/tiny-chat-Q4_K_M.gguf", "sha256": Q4_DIGEST, "size": 0, "source": "original"}
    ]
    model_dir = write_model(archive, record, creator="acme", model="tiny-chat")
    payload = model_dir / "gguf/tiny-chat-Q4_K_M.gguf"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"")
    return archive


def test_env_root_supplies_dest_per_tool(tmp_path: Path, gguf_archive: Path) -> None:
    views_root = tmp_path / "views-root"

    result = runner.invoke(
        app,
        ["views", str(gguf_archive), "--seed-store"],
        env={"LLM_PRESERVER_VIEWS": str(views_root)},
    )

    assert result.exit_code == 0
    assert (views_root / "ollama" / "blobs" / f"sha256-{Q4_DIGEST}").is_symlink()


def test_explicit_dest_wins_over_env_root(tmp_path: Path, gguf_archive: Path) -> None:
    explicit = tmp_path / "explicit-view"

    result = runner.invoke(
        app,
        ["views", str(gguf_archive), "--dest", str(explicit), "--seed-store"],
        env={"LLM_PRESERVER_VIEWS": str(tmp_path / "views-root")},
    )

    assert result.exit_code == 0
    assert (explicit / "blobs" / f"sha256-{Q4_DIGEST}").is_symlink()
    assert not (tmp_path / "views-root").exists()


def test_no_dest_and_no_env_is_a_usage_error(tmp_path: Path, gguf_archive: Path) -> None:
    result = runner.invoke(
        app,
        ["views", str(gguf_archive), "--seed-store"],
        env={"LLM_PRESERVER_VIEWS": ""},
    )

    assert result.exit_code == 2
    assert "LLM_PRESERVER_VIEWS" in output_of(result)
