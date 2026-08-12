"""CLI tests for the ``--repo`` rename — spec 0017 pass 1, criterion 2.

ADR 0003 makes a model directory a *repo* directory: its first
component is the repo owner, not the creator of some other model. The
CLI vocabulary follows — ``verify --model`` becomes ``verify --repo``,
with ``--model`` kept as an accepted alias (adjudicated 2026-08-11) so
nothing already scripted breaks. The alias prints a one-line note
naming ``--repo``; ``--help`` documents only ``--repo``.

``pull --model`` is the opposite case: it chose a *directory*, which
is no longer a choice anyone makes, so it is refused rather than
aliased.

Records here are built in the migrated shape (path == hub_id ==
source_repo) so that scoping and exit codes are what these tests
measure, not the layout verdict.

Not covered here, deliberately: ``remove --repo``. ``remove``'s model
id is a positional argument today, not an option, so criterion 2's
"the same flag" has no referent on that command — see the handoff note
raised with this test pass.
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
    """Create a model dir in the migrated shape: path == hub_id == source."""

    def _build(archive: Path, creator: str = "acme", model: str = "tiny-chat") -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["source_repo"] = f"https://huggingface.co/{creator}/{model}"
        record["artifacts"][0]["files"] = [
            {
                "path": PAYLOAD_REL,
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "size": len(PAYLOAD),
                "source": "original",
            }
        ]
        model_dir = write_model(archive, record, creator=creator, model=model)
        target = model_dir / PAYLOAD_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PAYLOAD)
        return model_dir

    return _build


@pytest.fixture
def two_model_archive(tmp_path: Path, build_model: Callable[..., Path]) -> Path:
    archive = init_archive_dir(tmp_path)
    build_model(archive)
    build_model(archive, creator="beta")
    return archive


def test_verify_repo_scopes_the_audit_to_one_directory(two_model_archive: Path) -> None:
    result = runner.invoke(app, ["verify", str(two_model_archive), "--repo", "acme/tiny-chat"])

    assert result.exit_code == 0, output_of(result)
    out = output_of(result)
    assert "acme/tiny-chat" in out
    assert "beta/coder" not in out


def test_verify_model_alias_still_scopes_the_audit(two_model_archive: Path) -> None:
    # Kept working on purpose: an existing cron line must not break.
    result = runner.invoke(app, ["verify", str(two_model_archive), "--model", "acme/tiny-chat"])

    assert result.exit_code == 0, output_of(result)
    out = output_of(result)
    assert "acme/tiny-chat" in out
    assert "beta/coder" not in out


def test_verify_model_alias_prints_one_note_naming_repo(two_model_archive: Path) -> None:
    result = runner.invoke(app, ["verify", str(two_model_archive), "--model", "acme/tiny-chat"])

    # Counted on result.output alone, never combined_output: this click
    # version already folds stderr into output, so the shared
    # output + stderr helper double-counts every stderr line and would
    # report two notes for one echo.
    notes = [line for line in click.unstyle(result.output).splitlines() if "--repo" in line]

    assert len(notes) == 1, output_of(result)
    assert "--model" in notes[0]


def test_verify_without_the_alias_prints_no_note(two_model_archive: Path) -> None:
    # The note belongs to the alias, not to the command: a run using
    # --repo must stay byte-identical to a scoped run before the rename.
    result = runner.invoke(app, ["verify", str(two_model_archive), "--repo", "acme/tiny-chat"])

    assert "--repo" not in output_of(result)


def test_verify_refuses_repo_and_model_together(two_model_archive: Path) -> None:
    # Two spellings of one option; supplying both is user input the
    # command cannot resolve, so exit 2 (0009's user-input domain). The
    # message names both spellings — click's bare "no such option"
    # would satisfy the exit code without telling the user anything.
    result = runner.invoke(
        app,
        [
            "verify",
            str(two_model_archive),
            "--repo",
            "acme/tiny-chat",
            "--model",
            "beta/coder",
        ],
    )
    out = output_of(result)

    assert result.exit_code == 2, out
    assert "--repo" in out
    assert "--model" in out


def test_verify_help_documents_repo_and_not_model() -> None:
    out = output_of(runner.invoke(app, ["verify", "--help"]))

    assert "--repo" in out
    assert "--model" not in out
