"""``pull --model``'s removal — spec 0017 criterion 2, deferred to pass 3.

Skipped on purpose. The flag is still *functional* in passes 1-2: it
overrides the canonical home that ``pull_grouping`` still proposes, and
57 call sites across 21 test files depend on it to pre-answer the
grouping prompt. Refusing it before pass 3 deletes grouping would force
every one of those tests to be rewritten twice — once to answer a prompt
that is about to disappear, and again when it does.

Pass 3 deleted ``pull_grouping`` and the flag together, and the skip
marker came off in the same change.
"""

import contextlib
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli.app import app

runner = CliRunner()


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
    assert result.exit_code == 0, output_of(result)
    return archive


class RefusingHubClient:
    """Hub seam that fails the test if the pull reaches the network.

    The ``--model`` refusal is argument handling: it must land before
    any hub call, which also keeps this test hermetic (a real client
    here would issue a live 404 for the fake repo id).
    """

    def repo_info(self, repo_id: str):
        raise AssertionError(f"--model must be refused before any hub call (got {repo_id})")

    def download(self, repo_id: str, filename: str, revision: str, dest_dir):
        raise AssertionError("download must not run")


@pytest.fixture
def no_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap the CLI's hub-client seam for one that refuses to be used."""
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", RefusingHubClient)


def test_pull_refuses_the_model_flag_with_exit_two(tmp_path: Path, no_hub: None) -> None:
    # The flag chose a directory; under ADR 0003 the destination is a
    # pure function of the typed repo id, so there is nothing to choose.
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(
        app,
        ["pull", "acme/tiny-chat", str(archive), "--model", "other/tiny-chat"],
    )

    assert result.exit_code == 2, output_of(result)


def test_pull_model_refusal_names_the_replacement(tmp_path: Path, no_hub: None) -> None:
    # Not click's bare "no such option": the message has to say what to
    # do instead — pull the repo id you want archived.
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(
        app,
        ["pull", "acme/tiny-chat", str(archive), "--model", "other/tiny-chat"],
    )

    out = output_of(result)
    assert "--model" in out
    assert "repo id" in out.lower()
