"""Spec 0026 pass 5: ``pull --progress`` on interrupts, a TTY, and a broken emitter.

Criterion 7 and the timer-thread note: Ctrl-C (130) and the spec 0025
SIGTERM unwind (143) stop the ticker *before* the resume command prints,
so the command stays the final line, and no ticker thread outlives the
pull. The stop event here ticks once more after ``set()``, so a ticker
stopped after the hint would print a line below it. Criterion 5: the
progress lines are the same on a TTY and off one. Criterion 6: an
emitter that raises changes nothing about the pull.

Harness: ``progress_cli_shapes``; archives live in ``tmp_path``.
"""

import json
import logging
import signal
import threading

import click
import pytest
import typer.testing
from progress_cli_shapes import (
    LOGGER_NAME,
    PREFIX,
    Q4_NAME,
    Q4_TICK_LINE,
    SUMMARY_LINE,
    TYPED,
    HookedHub,
    install_driven_ticker,
    install_fixed_clock,
    install_hub,
    lines_of,
    model_dir,
    new_archive,
    progress_lines,
    pull,
    tick_after,
    ticker_threads,
)

from llm_preserver.cli.pull_exec.termination import Terminated

LEAD_IN = "to continue this pull later"


@pytest.fixture
def thread_errors(monkeypatch):
    seen = []
    monkeypatch.setattr(threading, "excepthook", seen.append)
    return seen


def interrupted_hub(monkeypatch, error, *, tick_on_stop=True):
    """Q4 lands and ticks once; the second download raises ``error``."""
    stops = install_driven_ticker(monkeypatch, tick_on_stop=tick_on_stop)
    clock = install_fixed_clock(monkeypatch)
    client = HookedHub(
        on_downloaded=tick_after(Q4_NAME, stops, clock),
        download_error=error,
        fail_after_downloads=1,
    )
    install_hub(monkeypatch, client)
    return client


# --- interrupts: the resume command stays last (criterion 7) ---------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(KeyboardInterrupt(), 130, id="ctrl-c"),
        pytest.param(Terminated(signal.SIGTERM), 128 + signal.SIGTERM, id="sigterm"),
    ],
)
def test_an_interrupt_ends_on_the_resume_command_carrying_progress(
    tmp_path, monkeypatch, thread_errors, error, code
):
    client = interrupted_hub(monkeypatch, error)

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == code, click.unstyle(result.output)
    lines = lines_of(result)
    assert LEAD_IN in lines[-1]
    assert lines[-1].split()[-1] == "--progress"
    assert Q4_TICK_LINE in lines  # positive control: the ticker was live
    assert client.tickers_seen == [1, 1]
    assert ticker_threads() == []
    assert thread_errors == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(KeyboardInterrupt(), 130, id="ctrl-c"),
        pytest.param(Terminated(signal.SIGTERM), 128 + signal.SIGTERM, id="sigterm"),
    ],
)
def test_an_interrupted_pull_prints_no_summary(tmp_path, monkeypatch, error, code):
    interrupted_hub(monkeypatch, error, tick_on_stop=False)

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == code, click.unstyle(result.output)
    assert Q4_TICK_LINE in lines_of(result)  # positive control: the meter had started
    assert [line for line in progress_lines(lines_of(result)) if "transferred " in line] == []


def test_an_interactive_pull_interrupted_prints_the_hint_with_progress_twice(tmp_path, monkeypatch):
    # The transfer-start print (spec 0007) and the Ctrl-C repeat both
    # carry --progress; nothing follows the repeat.
    interrupted_hub(monkeypatch, KeyboardInterrupt())

    result = pull(new_archive(tmp_path), "--progress", stdin="*Q4_K_M*\ny\n")

    assert result.exit_code == 130, click.unstyle(result.output)
    lines = lines_of(result)
    hints = [line for line in lines if LEAD_IN in line]
    assert len(hints) == 2
    assert all(hint.split()[-1] == "--progress" for hint in hints)
    assert lines[-1] == hints[-1]


# --- TTY or not, the same lines (criterion 5) -------------------------------


def run_ticking_pull(monkeypatch, archive):
    stops = install_driven_ticker(monkeypatch)
    clock = install_fixed_clock(monkeypatch)
    install_hub(monkeypatch, HookedHub(on_downloaded=tick_after(Q4_NAME, stops, clock)))
    return pull(archive, *TYPED, "--progress")


def test_progress_lines_are_identical_on_a_tty_and_off_one(tmp_path, monkeypatch):
    piped = run_ticking_pull(monkeypatch, new_archive(tmp_path / "piped"))
    with monkeypatch.context() as tty:
        tty.setattr(typer.testing._NamedTextIOWrapper, "isatty", lambda self: True)
        on_tty = run_ticking_pull(monkeypatch, new_archive(tmp_path / "tty"))

    assert piped.exit_code == on_tty.exit_code == 0
    assert progress_lines(lines_of(piped)) == [Q4_TICK_LINE, SUMMARY_LINE]
    assert progress_lines(lines_of(on_tty)) == progress_lines(lines_of(piped))


def test_progress_lines_carry_no_carriage_return_or_escape(tmp_path, monkeypatch):
    with monkeypatch.context() as tty:
        tty.setattr(typer.testing._NamedTextIOWrapper, "isatty", lambda self: True)
        result = run_ticking_pull(monkeypatch, new_archive(tmp_path))

    raw = [line for line in result.output.split("\n") if LOGGER_NAME in line]
    assert len(raw) == 2
    assert not any("\r" in line or "\x1b" in line for line in raw)


# --- a broken emitter changes nothing (criterion 6) -------------------------


class RaiseOn(logging.Filter):
    """Make the progress logger raise for messages starting with ``prefix``.

    A logger-level filter runs inside ``Logger.info`` and its exception
    propagates to the caller, so this breaks the default emitter without
    knowing its name.
    """

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix
        self.raised = threading.Event()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage().startswith(self.prefix):
            self.raised.set()
            raise OSError("stderr went away")
        return True


@pytest.fixture
def breakable_logger():
    logger = logging.getLogger(LOGGER_NAME)
    added: list[logging.Filter] = []

    def install(prefix: str) -> RaiseOn:
        breaker = RaiseOn(prefix)
        logger.addFilter(breaker)
        added.append(breaker)
        return breaker

    yield install
    for breaker in added:
        logger.removeFilter(breaker)


def test_a_progress_line_that_cannot_be_written_changes_nothing(
    tmp_path, monkeypatch, thread_errors, breakable_logger
):
    breaker = breakable_logger("progress: ")
    stops = install_driven_ticker(monkeypatch)
    clock = install_fixed_clock(monkeypatch)

    def tick_without_waiting(name):
        if name == Q4_NAME:
            clock.now = 10.0
            stops[-1].tick(block=False)
            assert breaker.raised.wait(5.0)

    install_hub(monkeypatch, HookedHub(on_downloaded=tick_without_waiting))
    archive = new_archive(tmp_path)

    result = pull(archive, *TYPED, "--progress")

    out = click.unstyle(result.output)
    assert result.exit_code == 0, out
    assert breaker.raised.is_set()  # positive control: the emitter did raise
    assert (model_dir(archive) / "gguf" / Q4_NAME).is_file()
    assert json.loads((model_dir(archive) / "model-record.json").read_text())
    assert "Traceback" not in out
    assert thread_errors == []
    assert ticker_threads() == []


def test_a_summary_that_cannot_be_written_changes_nothing(
    tmp_path, monkeypatch, thread_errors, breakable_logger
):
    breaker = breakable_logger("transferred ")
    archive = new_archive(tmp_path)

    result = run_ticking_pull(monkeypatch, archive)

    out = click.unstyle(result.output)
    assert result.exit_code == 0, out
    assert breaker.raised.is_set()  # positive control: the summary did raise
    assert (model_dir(archive) / "model-record.json").is_file()
    assert lines_of(result)[-1].startswith("pulled ")
    assert "Traceback" not in out
    assert not [line for line in lines_of(result) if line.startswith(PREFIX + "transferred")]


def test_a_ticker_thread_that_cannot_start_changes_nothing(tmp_path, monkeypatch):
    # Criterion 6, found in review: a pids-limited container refuses new
    # threads with RuntimeError("can't start new thread"). The pull must
    # archive exactly as without the flag, with no lines and no traceback.
    real_start = threading.Thread.start

    def refuse_ticker(self):
        if self.name == "pull-progress-ticker":
            raise RuntimeError("can't start new thread")
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", refuse_ticker)
    install_hub(monkeypatch, HookedHub())
    archive = new_archive(tmp_path)

    result = pull(archive, *TYPED, "--progress")

    out = click.unstyle(result.output)
    assert result.exit_code == 0, out
    assert (model_dir(archive) / "gguf" / Q4_NAME).is_file()
    assert "Traceback" not in out
    assert out.rstrip().splitlines()[-1].startswith("pulled ")
