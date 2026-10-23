"""Spec 0025 at the CLI: exit 3 and one error line, ``--plan`` untouched, name collisions.

The refusal's observable shape (criteria 5, 7, 8 and 2's malformed
case): exit 3, exactly one ``error [local environment]`` line naming
the repo, holder host, pid, start time and marker path, no traceback.
``--plan`` neither creates a marker nor is refused by one (criterion
3). The selection-time name-collision refusal (Q4) and the Ollama-id
pin (Q3) live in ``test_cli_pull_claim_collisions.py`` (300-line rule).

Lines are counted on ``click.unstyle(result.output)`` alone — this
click folds stderr into ``output``, so the combined helper would double
them (``.claude/rules/python-code.md``). Everything runs in
``tmp_path`` with the hub seam faked. The chmod-000 test is skipped as
root.

Expected red (test-first): ``pull_claim`` does not exist (tests that
plant a holder fail on its import), and the rest fail on AssertionError
— today the second pull runs and exits 0.
"""

import contextlib
import logging
import os
from pathlib import Path

import click
import pytest
from pull_claim_shapes import (
    MARKER_NAME,
    OTHER_HOST,
    STARTED,
    THIS_HOST,
    ObservingHub,
    held_by_this_run,
    holder,
    marker_path,
    pin_host,
    plant,
)
from staging_shapes import REPO_ID, new_archive
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


def invoke(archive: Path, *args: str, repo_id: str = REPO_ID):
    # Drop the package handler a previous invoke bound to its own streams.
    logging.getLogger("llm_preserver").handlers.clear()
    return runner.invoke(app, ["pull", repo_id, str(archive), *args])


def everything(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return click.unstyle(out)


def error_lines(result) -> list[str]:
    return [
        line
        for line in click.unstyle(result.output).splitlines()
        if line.startswith("error [local environment]")
    ]


# --- criterion 5 ------------------------------------------------------------


def test_second_pull_of_a_held_repo_exits_3(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    raw = plant(marker_path(archive), holder(tmp_path, pid=os.getpid()))
    install_fake_hub(monkeypatch, ObservingHub(marker_path(archive)))

    result = invoke(archive, *PULL)

    assert result.exit_code == 3, everything(result)
    assert marker_path(archive).read_bytes() == raw
    # Control: the identical command succeeds once the holder is gone.
    marker_path(archive).unlink()
    assert invoke(archive, *PULL).exit_code == 0


def test_refusal_is_one_line_naming_repo_holder_start_and_path(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    plant(marker_path(archive), holder(tmp_path, pid=os.getpid()))
    install_fake_hub(monkeypatch, ObservingHub(marker_path(archive)))

    result = invoke(archive, *PULL)

    lines = error_lines(result)
    assert len(lines) == 1, click.unstyle(result.output)
    for fact in (REPO_ID, THIS_HOST, str(os.getpid()), STARTED, str(marker_path(archive))):
        assert fact in lines[0], fact
    assert "Traceback" not in everything(result)


# --- criterion 7 ------------------------------------------------------------


def test_foreign_host_refusal_tells_the_human_to_delete_the_marker(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    raw = plant(marker_path(archive), holder(tmp_path, hostname=OTHER_HOST))
    install_fake_hub(monkeypatch, ObservingHub(marker_path(archive)))

    result = invoke(archive, *PULL)

    assert result.exit_code == 3, everything(result)
    out = everything(result)
    assert OTHER_HOST in out
    assert str(marker_path(archive)) in out
    assert "delete" in out.lower()
    assert marker_path(archive).read_bytes() == raw


# --- criteria 2 and 8 -------------------------------------------------------


def test_malformed_marker_exits_3_naming_its_path(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    raw = plant(marker_path(archive), b"{not json")
    install_fake_hub(monkeypatch, ObservingHub(marker_path(archive)))

    result = invoke(archive, *PULL)

    assert result.exit_code == 3, everything(result)
    assert str(marker_path(archive)) in everything(result)
    assert "Traceback" not in everything(result)
    assert marker_path(archive).read_bytes() == raw


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_marker_exits_3_naming_its_path(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    plant(marker_path(archive), holder(tmp_path))
    marker_path(archive).chmod(0o000)
    install_fake_hub(monkeypatch, ObservingHub(marker_path(archive)))
    try:
        result = invoke(archive, *PULL)
    finally:
        marker_path(archive).chmod(0o644)

    assert result.exit_code == 3, everything(result)
    assert str(marker_path(archive)) in everything(result)
    assert "Traceback" not in everything(result)


# --- criterion 3: --plan ----------------------------------------------------


def test_plan_answers_while_another_pull_holds_the_repo(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    raw = plant(marker_path(archive), holder(tmp_path, pid=os.getpid()))
    install_fake_hub(monkeypatch, ObservingHub(marker_path(archive)))

    planned = invoke(archive, "--include", "*Q4_K_M*", "--plan")

    assert planned.exit_code == 0, everything(planned)
    assert "plan only" in everything(planned)
    assert marker_path(archive).read_bytes() == raw
    # Differential: the same live marker does stop a real pull.
    assert invoke(archive, *PULL).exit_code == 3


def test_plan_creates_no_marker(archive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    planner = ObservingHub(marker_path(archive))
    install_fake_hub(monkeypatch, planner)

    assert invoke(archive, "--include", "*Q4_K_M*", "--plan").exit_code == 0

    assert planner.at_repo_info == [None]
    assert not marker_path(archive).parent.exists()
    # Control: the real pull of the same selection does hold one.
    puller = ObservingHub(marker_path(archive))
    install_fake_hub(monkeypatch, puller)
    assert invoke(archive, *PULL).exit_code == 0
    assert held_by_this_run(puller.at_repo_info[0])


# --- criterion 10: a fresh pull's output is unchanged -----------------------


def test_fresh_pull_ends_as_before_with_the_marker_gone(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = ObservingHub(marker_path(archive))
    install_fake_hub(monkeypatch, client)

    result = invoke(archive, *PULL)

    assert result.exit_code == 0, everything(result)
    assert click.unstyle(result.output).splitlines()[-1].startswith(f"pulled {REPO_ID} into ")
    assert MARKER_NAME not in everything(result)
    assert held_by_this_run(client.at_download[0])
    assert not marker_path(archive).exists()
