"""Spec 0025 at the CLI: hub files and ids that must not reach a claim.

Q4 (plan round): staging uses hub paths verbatim, so a repo-root file
named ``.llm-preserver-pull.json`` (compared casefolded) would download
over the marker. When the selection includes one, ``pull`` and
``--plan`` refuse at selection time with exit 2 and download nothing; a
nested file of that name, or one present but not selected, pulls
normally (the controls). Q3: an id ``staging_dir_for`` cannot split (an
Ollama ``name:tag``) takes no claim, so the 0011/0013 error stays
byte-identical and no ``.staging/`` appears; that test drives the real
client offline, as ``test_cli_pull_ollama_shape`` does.

Expected red (test-first): the colliding file is archived today and
the pull exits 0. The Q3 assertions hold today (regression pin); its
in-test control — a valid id holding a claim — fails until the claim
exists. Split from ``test_cli_pull_claim.py`` (300-line rule).
"""

import contextlib
import logging
from pathlib import Path

import click
import pytest
from pull_claim_shapes import MARKER_NAME, ObservingHub, held_by_this_run, marker_path
from staging_shapes import Q4_BYTES, Q4_NAME, new_archive
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

PULL = ("--include", "*Q4_K_M*", "--yes")


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


def install_fake_hub(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke(archive: Path, *args: str, repo_id: str = "bartowski/tiny-chat-GGUF"):
    logging.getLogger("llm_preserver").handlers.clear()
    return runner.invoke(app, ["pull", repo_id, str(archive), *args])


def everything(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return click.unstyle(out)


# --- Q4: a hub file that would land on the marker ---------------------------


def colliding_hub(name: str) -> ObservingHub:
    return ObservingHub(
        Path("/nonexistent"),
        files=[
            (Q4_NAME, Q4_BYTES, True),
            ("README.md", b"# tiny-chat\n", False),
            (name, b'{"from": "the hub"}\n', False),
        ],
    )


@pytest.mark.parametrize(
    ("name", "args"),
    [
        (MARKER_NAME, ("--whole-repo", "--yes")),
        (".LLM-Preserver-Pull.JSON", ("--whole-repo", "--yes")),
        (MARKER_NAME, ("--include", "*Q4_K_M*", "--include", MARKER_NAME, "--yes")),
        (MARKER_NAME, ("--whole-repo", "--plan")),
    ],
    ids=["whole-repo", "case-variant", "include", "plan"],
)
def test_selected_root_file_named_like_the_marker_is_refused(
    archive: Path, monkeypatch: pytest.MonkeyPatch, name: str, args: tuple[str, ...]
) -> None:
    client = colliding_hub(name)
    install_fake_hub(monkeypatch, client)

    result = invoke(archive, *args)

    assert result.exit_code == 2, everything(result)
    assert "error [user input]" in everything(result)
    assert name in everything(result)
    assert client.download_calls == []


@pytest.mark.parametrize(
    ("name", "args"),
    [
        (f"sub/{MARKER_NAME}", ("--whole-repo", "--yes")),
        (MARKER_NAME, PULL),  # present in the repo, not selected
    ],
    ids=["nested", "unselected"],
)
def test_marker_named_file_that_cannot_collide_pulls_normally(
    archive: Path, monkeypatch: pytest.MonkeyPatch, name: str, args: tuple[str, ...]
) -> None:
    """The controls for the refusal above: same repo shape, exit 0, bytes moved."""
    client = colliding_hub(name)
    install_fake_hub(monkeypatch, client)

    result = invoke(archive, *args)

    assert result.exit_code == 0, everything(result)
    assert Q4_NAME in client.download_calls


# --- Q3: an id that cannot name a staging leaf takes no claim ---------------


def test_ollama_shaped_id_keeps_its_error_and_creates_no_staging(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    result = invoke(archive, "--include", "*", "--yes", repo_id="qwen3-vl:30b-a3b-instruct")

    out = everything(result)
    assert result.exit_code == 2, out
    assert "not a valid Hugging Face repo id" in out
    assert "--match-ollama" in out
    assert "Traceback" not in out
    assert not (archive / ".staging").exists()
    # Control: a valid id against the same archive does create (and hold) a leaf.
    client = ObservingHub(marker_path(archive))
    install_fake_hub(monkeypatch, client)
    assert invoke(archive, *PULL).exit_code == 0
    assert held_by_this_run(client.at_repo_info[0])
