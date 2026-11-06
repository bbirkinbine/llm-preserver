"""Spec 0026 pass 5: the ``--progress`` ticker thread, below the CLI.

``ProgressTicker`` owns one daemon thread that prints a progress line
every interval while the pull's meter has started (criterion 1), never
raises into the pull (criterion 6), and stops promptly on every exit
(the timer-thread note). Cadence is driven without real sleeps: the
stop event is injected through ``event_factory`` and its ``wait()``
returns False once per scripted tick, then True. The meter is a real
``PullProgress`` on a hand-moved clock, so each line is exact.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from llm_preserver.cli.pull_exec.progress_ticker import PROGRESS_INTERVAL_SECONDS, ProgressTicker
from llm_preserver.pull_progress import PullProgress

MIB = 1024 * 1024
THREAD_NAME = "pull-progress-ticker"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedStop:
    """``wait()`` returns False ``ticks`` times, then True.

    ``before_tick(n)`` runs inside the n-th (1-based) ticking wait, so a
    test moves the meter between lines; ``gate`` holds every wait until
    the test releases it. ``finished`` is set when the stop is reached.
    """

    def __init__(
        self,
        ticks: int,
        before_tick: Callable[[int], None] | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self.ticks = ticks
        self.before_tick = before_tick
        self.gate = gate
        self.timeouts: list[float | None] = []
        self.finished = threading.Event()
        self.was_set = False

    def wait(self, timeout: float | None = None) -> bool:
        self.timeouts.append(timeout)
        if self.gate is not None:
            self.gate.wait(5.0)
        if len(self.timeouts) <= self.ticks:
            if self.before_tick is not None:
                self.before_tick(len(self.timeouts))
            return False
        self.finished.set()
        return True

    def set(self) -> None:
        self.was_set = True

    def is_set(self) -> bool:
        return self.was_set


def ticker_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == THREAD_NAME and t.is_alive()]


def join_tickers() -> None:
    for thread in ticker_threads():
        thread.join(5.0)


@pytest.fixture
def thread_errors(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Exceptions that escaped any thread (Any: threading's hook args)."""
    seen: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", seen.append)
    return seen


def one_file_meter(clock: Clock) -> PullProgress:
    meter = PullProgress(clock=clock)
    meter.begin([("a.gguf", 100 * MIB)])
    meter.start_file(1, "a.gguf", declared_size=100 * MIB, already_staged=False)
    return meter


def run(meter: PullProgress, stop: ScriptedStop, **kwargs: Any) -> None:
    """Arm a ticker, let ``stop`` play out, and leave the context."""
    ticker = ProgressTicker(meter, event_factory=lambda: stop, **kwargs)
    with ticker:
        ticker.arm()
        assert stop.finished.wait(5.0), "the ticker never reached the stop"


def test_interval_is_fixed_at_thirty_seconds() -> None:
    assert PROGRESS_INTERVAL_SECONDS == 30


def test_ticker_waits_on_the_default_interval(thread_errors: list[Any]) -> None:
    stop = ScriptedStop(ticks=2)
    run(one_file_meter(Clock()), stop, emit=lambda line: None)

    assert stop.timeouts == [30, 30, 30]


def test_each_tick_emits_one_line_and_the_rate_covers_the_window_since_the_last(
    thread_errors: list[Any],
) -> None:
    clock = Clock()
    meter = one_file_meter(clock)
    moves = {1: (10.0, 10 * MIB), 2: (20.0, 30 * MIB)}

    def advance(tick: int) -> None:
        clock.now, received = moves[tick]
        meter.add_transfer_bytes(received)

    emitted: list[str] = []
    run(meter, ScriptedStop(ticks=2, before_tick=advance), emit=emitted.append, interval=7)

    # Line 2's rate is 30 MiB over the 10 s since line 1 (3.0 MiB/s),
    # not the 2.0 MiB/s since start; the estimate uses the average.
    assert emitted == [
        "progress: 10.0 MiB of 100.0 MiB (10%), 1.0 MiB/s, ~1m30s left, downloading 1 of 1: a.gguf",
        "progress: 40.0 MiB of 100.0 MiB (40%), 3.0 MiB/s, ~30s left, downloading 1 of 1: a.gguf",
    ]
    assert thread_errors == []


def test_ticks_before_the_meter_starts_emit_nothing(thread_errors: list[Any]) -> None:
    clock = Clock()
    meter = PullProgress(clock=clock)

    def start_on_second_tick(tick: int) -> None:
        if tick == 2:
            meter.begin([("a.gguf", 4)])
            meter.start_file(1, "a.gguf", declared_size=4, already_staged=False)

    emitted: list[str] = []
    run(meter, ScriptedStop(ticks=3, before_tick=start_on_second_tick), emit=emitted.append)

    assert len(emitted) == 2  # ticks 2 and 3; tick 1 saw an unstarted meter


def test_default_emit_logs_info_on_the_pull_progress_logger(
    caplog: pytest.LogCaptureFixture, thread_errors: list[Any]
) -> None:
    caplog.set_level(logging.INFO, logger="llm_preserver")
    run(one_file_meter(Clock()), ScriptedStop(ticks=1))

    records = [r for r in caplog.records if r.getMessage().startswith("progress: ")]
    assert len(records) == 1
    assert records[0].name == "llm_preserver.pull_progress"
    assert records[0].levelno == logging.INFO


def test_arm_starts_one_daemon_thread_named_pull_progress_ticker_even_twice(
    thread_errors: list[Any],
) -> None:
    gate = threading.Event()
    stop = ScriptedStop(ticks=0, gate=gate)
    ticker = ProgressTicker(
        one_file_meter(Clock()), emit=lambda line: None, event_factory=lambda: stop
    )
    with ticker:
        ticker.arm()
        ticker.arm()
        threads = ticker_threads()
        gate.set()
        assert stop.finished.wait(5.0)

    assert len(threads) == 1
    assert threads[0].daemon


def test_leaving_the_context_stops_the_thread(thread_errors: list[Any]) -> None:
    stop = ScriptedStop(ticks=1)
    run(one_file_meter(Clock()), stop, emit=lambda line: None)

    assert stop.was_set
    assert ticker_threads() == []


def test_a_ticker_never_armed_starts_no_thread_and_exits_cleanly(thread_errors: list[Any]) -> None:
    stop = ScriptedStop(ticks=1)
    with ProgressTicker(
        one_file_meter(Clock()), emit=lambda line: None, event_factory=lambda: stop
    ):
        assert ticker_threads() == []

    assert stop.timeouts == []
    assert thread_errors == []


def test_an_emit_that_raises_stops_the_ticker_quietly(
    caplog: pytest.LogCaptureFixture, thread_errors: list[Any]
) -> None:
    caplog.set_level(logging.DEBUG, logger="llm_preserver")
    calls: list[str] = []

    def broken_emit(line: str) -> None:
        calls.append(line)
        raise OSError("stderr went away")

    stop = ScriptedStop(ticks=3)
    ticker = ProgressTicker(one_file_meter(Clock()), emit=broken_emit, event_factory=lambda: stop)
    with ticker:
        ticker.arm()
        join_tickers()

    assert len(calls) == 1  # stopped after the first failure, no retries
    assert len(stop.timeouts) == 1
    assert thread_errors == []
    assert any(
        r.levelno == logging.DEBUG and r.name.startswith("llm_preserver") for r in caplog.records
    )


class UnrenderableMeter(PullProgress):
    """A started meter whose snapshot the renderer cannot read."""

    def snapshot(self) -> Any:  # Any: deliberately not a ProgressSnapshot
        return object()


def test_a_render_failure_stops_the_ticker_quietly(thread_errors: list[Any]) -> None:
    meter = UnrenderableMeter(clock=Clock())
    meter.begin([("a.gguf", 4)])
    emitted: list[str] = []
    stop = ScriptedStop(ticks=3)
    ticker = ProgressTicker(meter, emit=emitted.append, event_factory=lambda: stop)
    with ticker:
        ticker.arm()
        join_tickers()

    assert emitted == []
    assert len(stop.timeouts) == 1
    assert thread_errors == []


def test_exit_joins_with_a_bounded_timeout_and_never_raises(thread_errors: list[Any]) -> None:
    # An emit stuck on a dead stream must not hold the pull's exit
    # hostage: __exit__ gives the thread about a second, then returns.
    entered, release = threading.Event(), threading.Event()

    def stuck_emit(line: str) -> None:
        entered.set()
        release.wait(10.0)

    stop = ScriptedStop(ticks=1)
    ticker = ProgressTicker(one_file_meter(Clock()), emit=stuck_emit, event_factory=lambda: stop)
    try:
        with ticker:
            ticker.arm()
            assert entered.wait(5.0)
            started = time.monotonic()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        join_tickers()

    assert elapsed < 3.0
    assert stop.was_set
