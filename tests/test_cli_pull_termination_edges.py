"""Spec 0025 review round: the SIGTERM / SIGHUP unwind at its edges.

Continues ``test_cli_pull_termination.py``. Each test is a case the
first version got wrong, reproduced by a reviewer before it was fixed:

- an inherited ``SIG_IGN`` (``nohup``) was overridden, so a pull the
  human had protected from hangups died at exit 129 when the terminal
  closed;
- a signal at a y/N prompt was folded by click into ``Abort`` and
  reported as the human's own decline (exit 0, "declined"), or as an
  unanswerable prompt (exit 2) — a signal is not an answer;
- after a hangup the terminal is gone, and an echo that cannot be
  printed must not turn the exit into a traceback;
- a second signal landing inside the release escaped the ``finally``
  and left the marker, which every other host then refuses on.

Every test installs recording handlers first (the ``recorded`` fixture
below), so a regression shows up as a wrong exit code rather than as
the default action killing the test process.
"""

import io
import logging
import os
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest
import typer
from pull_claim_shapes import ObservingHub, held_by_this_run, marker_path
from staging_shapes import REPO_ID, new_archive
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

SIGHUP = getattr(signal, "SIGHUP", None)
needs_sighup = pytest.mark.skipif(SIGHUP is None, reason="no SIGHUP here")


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


@pytest.fixture
def recorded() -> Iterator[list[int]]:
    """Replace the default (fatal) actions with recorders for the test."""
    seen: list[int] = []
    sigs = [signal.SIGTERM] + ([SIGHUP] if SIGHUP is not None else [])
    previous = {sig: signal.signal(sig, lambda signum, frame: seen.append(signum)) for sig in sigs}
    yield seen
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def invoke(
    monkeypatch: pytest.MonkeyPatch,
    archive: Path,
    client: object,
    *args: str,
    stdin: io.BytesIO | None = None,
) -> Any:
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)
    logging.getLogger("llm_preserver").handlers.clear()
    if stdin is not None:
        # typer's runner takes only str/bytes input and wraps it in a
        # plain BytesIO; hand it the signalling stream instead.
        import typer.testing

        monkeypatch.setattr(typer.testing, "make_input_stream", lambda _input, _charset: stdin)
    argv = ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", *args]
    return runner.invoke(app, argv)


class SignalAtPrompt(io.BytesIO):
    """Stdin that delivers ``signum`` when the y/N prompt reads its answer.

    Becomes the runner's stdin. ``CliRunner`` installs its own
    prompt reader for the length of ``invoke``, so patching
    ``visible_prompt_func`` is overwritten and never fires; the read of
    the runner's own stdin is the seam that survives. The signal then
    raises inside that read, and click folds it into ``Abort`` exactly
    as it would on a terminal. ``read(0)`` is the runner's binary-stream
    probe and stays quiet.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(b"y\n")  # what the prompt would get if nothing intervened
        self.signum = signum
        self.fired = False

    def _signal(self, n: int | None) -> None:
        if n != 0 and not self.fired:
            self.fired = True
            os.kill(os.getpid(), self.signum)

    def read(self, n: int | None = -1) -> bytes:
        self._signal(n)
        return super().read(n)

    def read1(self, n: int | None = -1) -> bytes:
        self._signal(n)
        return super().read1(n)

    def readline(self, n: int | None = -1) -> bytes:
        self._signal(n)
        return super().readline(n)

    def readinto(self, buffer: Any) -> int:  # Any: any writable buffer
        self._signal(len(buffer))
        return super().readinto(buffer)


@needs_sighup
def test_an_ignored_sighup_stays_ignored_so_nohup_pulls_finish(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert SIGHUP is not None
    previous = signal.signal(SIGHUP, signal.SIG_IGN)  # what `nohup` hands the process
    try:
        client = ObservingHub(
            marker_path(archive), on_download=lambda: os.kill(os.getpid(), SIGHUP)
        )

        result = invoke(monkeypatch, archive, client, "--yes")

        assert result.exit_code == 0, click.unstyle(result.output)
        assert client.at_download, "positive control: the hangup came mid-transfer"
        assert signal.getsignal(SIGHUP) is signal.SIG_IGN  # and is still ignored after
    finally:
        signal.signal(SIGHUP, previous)


@pytest.mark.parametrize("interactive", [True, False], ids=["terminal", "piped"])
def test_sigterm_at_the_size_prompt_exits_143_not_a_decline(
    interactive: bool,
    archive: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[int],
) -> None:
    import llm_preserver.cli.pull_exec.confirmations as confirmations

    monkeypatch.setattr(confirmations, "is_interactive", lambda stream: interactive)
    stdin = SignalAtPrompt(signal.SIGTERM)
    client = ObservingHub(marker_path(archive))

    result = invoke(monkeypatch, archive, client, stdin=stdin)  # no --yes: the prompt asks

    out = click.unstyle(result.output)
    assert stdin.fired, "positive control: the signal came at the prompt"
    assert recorded == [], "the pull's own handler, not the test's, took the signal"
    assert result.exit_code == 128 + signal.SIGTERM, out
    assert "nothing pulled" not in out  # nobody declined anything
    assert "not interactive" not in out
    assert client.download_calls == []
    assert not marker_path(archive).exists()


@needs_sighup
def test_hangup_at_the_prompt_with_the_terminal_gone_exits_129(
    archive: Path, monkeypatch: pytest.MonkeyPatch, recorded: list[int]
) -> None:
    assert SIGHUP is not None
    import llm_preserver.cli.pull_exec.confirmations as confirmations

    monkeypatch.setattr(confirmations, "is_interactive", lambda stream: True)
    stdin = SignalAtPrompt(SIGHUP)
    real_echo = typer.echo

    def dead_terminal_echo(message: Any = None, *args: Any, **kwargs: Any) -> None:
        if not message:  # the newline after an aborted prompt
            raise OSError(5, "Input/output error")
        real_echo(message, *args, **kwargs)

    monkeypatch.setattr(typer, "echo", dead_terminal_echo)

    result = invoke(monkeypatch, archive, ObservingHub(marker_path(archive)), stdin=stdin)

    assert stdin.fired, "positive control: the hangup came at the prompt"
    assert result.exit_code == 128 + SIGHUP, click.unstyle(result.output)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert not marker_path(archive).exists()


def test_a_resume_hint_that_cannot_be_printed_still_exits_128_plus_n(
    archive: Path, monkeypatch: pytest.MonkeyPatch, recorded: list[int]
) -> None:
    # The guard is the suppress around the hint echo in run_pull: after a
    # hangup the terminal is gone and every write raises EIO.
    terminal_gone = False
    real_echo = typer.echo

    def echo(message: Any = None, *args: Any, **kwargs: Any) -> None:
        if terminal_gone:
            raise OSError(5, "Input/output error")
        real_echo(message, *args, **kwargs)

    monkeypatch.setattr(typer, "echo", echo)

    def hang_up() -> None:
        nonlocal terminal_gone
        terminal_gone = True
        os.kill(os.getpid(), signal.SIGTERM)

    client = ObservingHub(marker_path(archive), on_download=hang_up)

    result = invoke(monkeypatch, archive, client, "--yes")

    assert result.exit_code == 128 + signal.SIGTERM, repr(result.exception)
    assert held_by_this_run(client.at_download[0])  # positive control: it was held
    assert not marker_path(archive).exists()


def test_a_second_signal_during_release_still_removes_the_marker(
    archive: Path, monkeypatch: pytest.MonkeyPatch, recorded: list[int]
) -> None:
    # A closed terminal can deliver SIGHUP twice: the shell forwards one
    # to its jobs, the kernel sends another when the session leader
    # exits. The second landing inside the release must not strand the
    # marker; modelled as the signal arriving at the release's own read.
    import llm_preserver.pull_claim as pull_claim
    from llm_preserver.cli.pull_exec.termination import Terminated

    real_read = pull_claim.read_marker_bytes
    reads: list[Path] = []

    def read_then_signal(path: Path) -> bytes:
        reads.append(path)
        if len(reads) == 1:
            raise Terminated(signal.SIGTERM)
        return real_read(path)

    monkeypatch.setattr(pull_claim, "read_marker_bytes", read_then_signal)

    result = invoke(monkeypatch, archive, ObservingHub(marker_path(archive)), "--yes")

    assert reads, "positive control: the release read the marker"
    assert result.exit_code == 128 + signal.SIGTERM, click.unstyle(result.output)
    assert not marker_path(archive).exists()
