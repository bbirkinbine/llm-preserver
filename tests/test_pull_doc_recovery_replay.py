"""Spec 0020: the printed recovery command, executed rather than read.

Every other test in this feature pins what the line *says*. These pin
what it *does* — the spec's headline criterion is "pasting it and
pressing enter is the whole recovery", and a line that is byte-correct
and still does not clear the stop would satisfy the text assertions
completely.

Two shapes, because the composed command differs between them and the
whole-repo one had no coverage at all: both reviewers found that
``select_all`` could be pinned to False in the handler with all 1343
tests green. That regression is not cosmetic. A command carrying
neither ``--include`` nor ``--whole-repo`` drops the human into the
interactive file listing on paste, and ``pull_prepare`` sets
``relocate_docs=not select_all`` — so the doc would land at a different
path than the record entry that conflicts, archiving a second copy of
the card and never resolving the original stop.

Fixtures come from test_pull_doc_recovery (the repo's established
cross-test import pattern); no network.
"""

import logging
import shlex

import click
import pytest
from test_pull_doc_recovery import (
    BASE_MODEL,
    README_V1,
    README_V2,
    REPO_ID,
    archived,  # noqa: F401 — pytest fixture
    changed_hub,  # noqa: F401 — pytest fixture
    init_archive_dir,
    install_fake_hub,
    recovery_lines,
    runner,
    stderr_text,
    walk_client,
)

from llm_preserver.cli import app

DOCS_REL = "gguf/docs/bartowski--tiny-chat-GGUF"


@pytest.fixture
def whole_repo_archived(tmp_path, monkeypatch, fake_hub_factory):
    """An archive whose first pull was a whole-repo snapshot.

    The selective ``archived`` fixture cannot stand in here: with
    ``--whole-repo``, ``pull_prepare`` sets ``relocate_docs=False`` and
    the card lands in-tree rather than under ``docs/<source-repo>/``, so
    a whole-repo re-pull of a selectively archived model conflicts with
    nothing and exits 0. The two layouts must match for the doc stop to
    fire at all — which is the same divergence that makes a dropped
    ``select_all`` in the composed command dangerous.
    """
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, walk_client(fake_hub_factory, README_V1))
    result = runner.invoke(app, ["pull", REPO_ID, str(archive), "--whole-repo", "--yes"])
    assert result.exit_code == 0, click.unstyle(result.output)
    logging.getLogger("llm_preserver").handlers.clear()
    return archive


def replay(command_line: str, archive):
    """Execute a printed recovery command the way a paste would.

    Splits with ``shlex`` (the same grammar the line was quoted for),
    drops ``argv[0]``, and answers the size confirmation — the composed
    command deliberately never carries ``--yes``, so a real paste is
    asked how much it is about to download.
    """
    argv = shlex.split(command_line)
    assert argv[0] == "llm-preserver", argv
    logging.getLogger("llm_preserver").handlers.clear()
    return runner.invoke(app, argv[1:], input="y\n")


def sole_recovery_command(result) -> str:
    """The one recovery line's command half, lead-in stripped."""
    err = stderr_text(result)
    assert result.exit_code == 5, err
    lines = recovery_lines(err)
    assert len(lines) == 1, err
    return lines[0].split(": ", 1)[1]


def test_pasting_the_printed_command_resolves_the_stop(archived, changed_hub):  # noqa: F811
    # The criterion no text assertion can reach: the line works.
    stopped = runner.invoke(app, ["pull", REPO_ID, str(archived), "--include", "*a*", "--yes"])
    command = sole_recovery_command(stopped)

    replayed = replay(command, archived)

    assert replayed.exit_code == 0, click.unstyle(replayed.output)
    archived_readme = archived / "models" / "bartowski" / "tiny-chat-GGUF" / DOCS_REL / "README.md"
    assert archived_readme.read_bytes() == README_V2


def test_a_whole_repo_pull_replays_as_whole_repo(whole_repo_archived, changed_hub):  # noqa: F811
    # Pins select_all through the composer. Without this, pinning it to
    # False in flow.py leaves the whole suite green while the printed
    # command silently becomes a different pull.
    archive = whole_repo_archived
    stopped = runner.invoke(app, ["pull", REPO_ID, str(archive), "--whole-repo", "--yes"])
    command = sole_recovery_command(stopped)

    assert command == (
        f"llm-preserver pull {REPO_ID} {archive.resolve()} --whole-repo --refresh-docs"
    )
    assert "--include" not in command

    replayed = replay(command, archive)

    assert replayed.exit_code == 0, click.unstyle(replayed.output)
    assert (
        archive / "models" / "bartowski" / "tiny-chat-GGUF" / "gguf" / "README.md"
    ).read_bytes() == README_V2


def test_the_replayed_command_carries_the_curator_flags(archived, changed_hub):  # noqa: F811
    # --role and --base-model are curator judgment: a replay that
    # dropped them would resolve the stop and silently lose the
    # assertion the original pull was making.
    stopped = runner.invoke(
        app,
        [
            "pull",
            REPO_ID,
            str(archived),
            "--include",
            "*a*",
            "--role",
            "chat",
            "--base-model",
            BASE_MODEL,
            "--yes",
        ],
    )
    command = sole_recovery_command(stopped)

    replayed = replay(command, archived)

    assert replayed.exit_code == 0, click.unstyle(replayed.output)
    record = (archived / "models" / "bartowski" / "tiny-chat-GGUF" / "model-record.json").read_text(
        encoding="utf-8"
    )
    assert "chat" in record
    assert BASE_MODEL in record
