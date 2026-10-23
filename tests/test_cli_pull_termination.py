"""Spec 0025: SIGTERM and SIGHUP mid-pull unwind like Ctrl-C.

Python runs no ``finally`` on an unhandled signal, so before this a
plain ``kill`` or a closed terminal or SSH session left the claim marker
behind, and every other machine was refused until a human deleted it.
While ``pull_model`` runs, both signals now raise ``Terminated`` (a
``KeyboardInterrupt``): the claim is released, staged bytes stay for
the resume, the resume command is the last line, and the exit is
128 + the signal number.

Every test installs a recording handler for the signal first, so a
regression (no unwind installed) shows up as a pull that finished at
exit 0 rather than as the default action killing the test process.
SIGHUP tests skip where the platform has no SIGHUP.
"""

import logging
import os
import signal
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from pull_claim_shapes import ObservingHub, held_by_this_run, marker_path
from staging_shapes import REPO_ID, new_archive, staging_leaf
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

PULL = ("--include", "*Q4_K_M*", "--yes")
SIGNALS = [
    pytest.param(signal.SIGTERM, id="SIGTERM"),
    pytest.param(
        getattr(signal, "SIGHUP", signal.SIGTERM),
        id="SIGHUP",
        marks=pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP here"),
    ),
]


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


@pytest.fixture
def recorded() -> Iterator[list[int]]:
    """Replace the default (fatal) actions with recorders for the test."""
    seen: list[int] = []
    sigs = [signal.SIGTERM] + ([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])
    previous = {sig: signal.signal(sig, lambda signum, frame: seen.append(signum)) for sig in sigs}
    yield seen
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def invoke(monkeypatch: pytest.MonkeyPatch, archive: Path, client: object):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)
    logging.getLogger("llm_preserver").handlers.clear()
    return runner.invoke(app, ["pull", REPO_ID, str(archive), *PULL])


@pytest.mark.parametrize("signum", SIGNALS)
def test_signal_mid_transfer_releases_the_claim_and_exits_128_plus_n(
    signum: int, archive: Path, monkeypatch: pytest.MonkeyPatch, recorded: list[int]
) -> None:
    calls: list[int] = []

    def signal_on_second_download() -> None:
        calls.append(1)
        if len(calls) == 2:  # the first file is staged by now
            os.kill(os.getpid(), signum)

    client = ObservingHub(marker_path(archive), on_download=signal_on_second_download)

    result = invoke(monkeypatch, archive, client)

    assert recorded == [], "the pull's own handler, not the test's, took the signal"
    assert result.exit_code == 128 + signum, click.unstyle(result.output)
    assert held_by_this_run(client.at_download[0])  # positive control: it was held
    assert not marker_path(archive).exists()
    assert len(calls) == 2  # positive control: the signal came mid-transfer
    assert any(staging_leaf(archive).rglob("*"))  # staged bytes stay for the resume
    last = click.unstyle(result.output).strip().splitlines()[-1]
    assert "llm-preserver pull " in last and REPO_ID in last  # the resume command
    assert "Traceback" not in click.unstyle(result.output)


def test_handlers_are_restored_after_the_pull(
    archive: Path, monkeypatch: pytest.MonkeyPatch, recorded: list[int]
) -> None:
    before = signal.getsignal(signal.SIGTERM)

    result = invoke(monkeypatch, archive, ObservingHub(marker_path(archive)))

    assert result.exit_code == 0, click.unstyle(result.output)
    assert signal.getsignal(signal.SIGTERM) is before
    os.kill(os.getpid(), signal.SIGTERM)  # after the pull: the recorder, not an unwind
    assert recorded == [signal.SIGTERM]
