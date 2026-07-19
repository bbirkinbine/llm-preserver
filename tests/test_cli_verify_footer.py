"""CLI tests for the plain-``verify`` leftover footer (spec 0012).

The informational one-line note a routine ``verify`` prints when
``.staging/`` holds abandoned downloads — split from
test_cli_verify_staging.py (the ``--staging`` deep view) to keep each
file under the size cap. Output is unstyled (``click.unstyle``) before
substring asserts (rich-ANSI-in-CI rule).
"""

import contextlib
import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.pull_prepare import STAGING_DIRNAME

runner = CliRunner()

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"
EVIL = b"evil payload bytes"  # same length as PAYLOAD; only the hash differs


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


def entry_for(rel_path: str, content: bytes) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    return {
        "path": rel_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "source": "original",
    }


def init_archive_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def make_staging(archive: Path, creator: str, model: str, *sizes: int) -> Path:
    """Create ``.staging/<creator>/<model>/`` with one file per size."""
    leaf = archive / STAGING_DIRNAME / creator / model
    leaf.mkdir(parents=True)
    for index, size in enumerate(sizes):
        (leaf / f"part-{index}.incomplete").write_bytes(b"x" * size)
    return leaf


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


def clean_model(build_model: Callable[..., Path], archive: Path, creator: str, model: str) -> Path:
    return build_model(
        archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD}, creator, model
    )


def drift_model(build_model: Callable[..., Path], archive: Path, creator: str, model: str) -> Path:
    """A model whose disk bytes mismatch the record hash: exit-5 drift."""
    return build_model(
        archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: EVIL}, creator, model
    )


def test_plain_verify_prints_footer_with_clean_models_exit_zero(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    clean_model(build_model, archive, "acme", "tiny-chat")
    make_staging(archive, "beta", "coder", 3000, 1500)
    make_staging(archive, "zeta", "model", 1200, 1000)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0  # exit unchanged; the footer is informational
    out = output_of(result)
    assert "2 abandoned downloads in .staging/" in out
    assert "verify --staging" in out


def test_plain_verify_prints_footer_even_when_drift_exits_five(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    drift_model(build_model, archive, "acme", "tiny-chat")
    make_staging(archive, "beta", "coder", 3000, 1500)
    make_staging(archive, "zeta", "model", 1200, 1000)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5  # the drift verdict still governs the exit
    out = output_of(result)
    assert "2 abandoned downloads in .staging/" in out  # but the footer is not hidden
    assert "verify --staging" in out


def test_quick_verify_shows_footer(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    archive = init_archive_dir(tmp_path)
    clean_model(build_model, archive, "acme", "tiny-chat")
    make_staging(archive, "beta", "coder", 3000, 1500)
    make_staging(archive, "zeta", "model", 1200, 1000)

    result = runner.invoke(app, ["verify", str(archive), "--quick"])

    assert result.exit_code == 0
    out = output_of(result)
    assert "2 abandoned downloads in .staging/" in out
    assert "verify --staging" in out


def test_staging_only_archive_still_prints_footer(tmp_path: Path) -> None:
    """Only a staging leftover, no models/ entry: not silently 'empty'."""
    archive = init_archive_dir(tmp_path)
    make_staging(archive, "beta", "coder", 3000, 1500)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    out = output_of(result)
    assert "1 abandoned download" in out  # matches singular and plural
    assert "verify --staging" in out


def test_plain_verify_footer_scoped_to_named_model(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """--model scopes the footer count to that model's leftover."""
    archive = init_archive_dir(tmp_path)
    clean_model(build_model, archive, "acme", "tiny-chat")
    make_staging(archive, "acme", "tiny-chat", 3000, 1500)
    make_staging(archive, "beta", "coder", 1200, 1000)

    result = runner.invoke(app, ["verify", str(archive), "--model", "acme/tiny-chat"])

    assert result.exit_code == 0
    out = output_of(result)
    assert "1 abandoned download" in out  # only the named model's leftover
    assert "2 abandoned" not in out
    assert "beta/coder" not in out


def test_plain_verify_footer_skipped_on_unreadable_staging(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """An unreadable .staging/ never crashes an otherwise-successful audit."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    archive = init_archive_dir(tmp_path)
    clean_model(build_model, archive, "acme", "tiny-chat")
    make_staging(archive, "beta", "coder", 3000)
    staging_root = archive / STAGING_DIRNAME
    staging_root.chmod(0o000)
    try:
        result = runner.invoke(app, ["verify", str(archive)])
    finally:
        staging_root.chmod(0o755)

    assert result.exit_code == 0  # audit succeeded; footer silently skipped
    assert not isinstance(result.exception, OSError)  # no traceback
