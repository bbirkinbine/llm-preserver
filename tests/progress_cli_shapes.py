"""Shared harness for the spec 0026 pass 5 CLI tests (``--progress``).

Plain helpers, no fixtures, like ``staging_shapes.py``. Nothing here
sleeps and nothing waits on the real 30-second interval: the ticker's
stop event is replaced by ``DrivenStop`` (one progress line per
``tick()``, released from inside the fake download so the meter state
at that moment is known), and the meter's clock by ``Clock`` (rates and
durations become exact). Both are injected by wrapping the *class*
``__init__`` of ``ProgressTicker`` / ``PullProgress``, so the patch
holds however ``run_pull`` imports them. The meter itself is real.

Every archive lives in ``tmp_path``; never a real one.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
from fake_hub import DEFAULT_HUB_FILES, FakeHubClient
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
README_NAME = "README.md"
LOGGER_NAME = "llm_preserver.pull_progress"
PREFIX = f"INFO {LOGGER_NAME}: "
TICKER_THREAD = "pull-progress-ticker"
TYPED = ("--include", "*Q4_K_M*", "--yes")

# The default selection: Q4 (15 B) plus README.md (22 B, docs ride
# along) = 37 B over 2 downloads. One tick right after Q4's bytes land,
# with the clock moved from 0 s (begin) to 10 s: 15 B / 10 s = 1 B/s
# (truncated), 22 B left at the 1.5 B/s average = ~15s.
TICK_AT = 10.0
Q4_TICK_LINE = (
    PREFIX + "progress: 15 B of 37 B (40%), 1 B/s, ~15s left, downloading 1 of 2: " + Q4_NAME
)
SUMMARY_LINE = PREFIX + "transferred 37 B (archived 37 B) in 10s, 3 B/s average"


class Clock:
    """A meter clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class DrivenStop:
    """Stand-in for the ticker's stop event: it ticks only when told to.

    ``wait()`` blocks until ``tick()`` releases one tick (returns False:
    the ticker emits a line) or ``set()`` stops it (returns True). With
    ``tick_on_stop`` the first wait after ``set()`` still returns False
    once, so a ticker stopped *late* emits a line where a test can see
    it. ``timeouts`` records every interval the ticker waited on.
    """

    def __init__(self, *, tick_on_stop: bool = False) -> None:
        self._cond = threading.Condition()
        self._permits = 0
        self._released = 0
        self._entries = 0
        self._stopped = False
        self._tick_on_stop = tick_on_stop
        self.timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        with self._cond:
            self.timeouts.append(timeout)
            self._entries += 1
            self._cond.notify_all()
            while not self._permits and not self._stopped:
                if not self._cond.wait(5.0):  # never hang a test
                    return True
            if self._permits:
                self._permits -= 1
                return False
            if self._tick_on_stop:
                self._tick_on_stop = False
                return False
            return True

    def set(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def is_set(self) -> bool:
        with self._cond:
            return self._stopped

    def tick(self, *, block: bool = True) -> bool:
        """Release one tick; with ``block``, wait until its line is out."""
        with self._cond:
            self._released += 1
            self._permits += 1
            self._cond.notify_all()
            if not block:
                return True
            target = self._released + 1  # the ticker is back in wait()
            return self._cond.wait_for(lambda: self._entries >= target, timeout=2.0)


def install_driven_ticker(monkeypatch: Any, *, tick_on_stop: bool = False) -> list[DrivenStop]:
    """Give every ``ProgressTicker`` built from now on its own ``DrivenStop``."""
    from llm_preserver.cli.pull_exec.progress_ticker import ProgressTicker

    stops: list[DrivenStop] = []
    # Wrap the pristine __init__ even when a test installs twice, or the
    # inner wrapper would override this call's stop event.
    original = getattr(ProgressTicker.__init__, "pristine", ProgressTicker.__init__)

    def driven_init(self: Any, meter: Any, *args: Any, **kwargs: Any) -> None:
        stop = DrivenStop(tick_on_stop=tick_on_stop)
        stops.append(stop)
        kwargs["event_factory"] = lambda: stop
        original(self, meter, *args, **kwargs)

    driven_init.pristine = original  # type: ignore[attr-defined]
    monkeypatch.setattr(ProgressTicker, "__init__", driven_init)
    return stops


def install_fixed_clock(monkeypatch: Any) -> Clock:
    """Make every ``PullProgress`` built from now on read ``Clock``."""
    from llm_preserver.pull_progress import PullProgress

    clock = Clock()
    original = getattr(PullProgress.__init__, "pristine", PullProgress.__init__)

    def clocked_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, clock=clock)

    clocked_init.pristine = original  # type: ignore[attr-defined]
    monkeypatch.setattr(PullProgress, "__init__", clocked_init)
    return clock


class HookedHub(FakeHubClient):
    """``FakeHubClient`` that calls ``on_downloaded(filename)`` after each file.

    The hook runs once the file's bytes have reached the sink and before
    the pull hashes it, so a tick there sees the ``downloading`` phase.
    It records which ticker threads were alive at each download in
    ``tickers_seen``, the positive control for the thread-gone asserts.
    """

    def __init__(
        self, *, on_downloaded: Callable[[str], None] | None = None, **kwargs: Any
    ) -> None:
        kwargs.setdefault("files", DEFAULT_HUB_FILES)
        kwargs.setdefault("base_model", None)
        super().__init__(**kwargs)
        self.on_downloaded = on_downloaded
        self.tickers_seen: list[int] = []

    def download(
        self, repo_id: str, filename: str, revision: str, dest_dir: Any, **kw: Any
    ) -> Path:
        self.tickers_seen.append(len(ticker_threads()))
        local = super().download(repo_id, filename, revision, dest_dir, **kw)
        if self.on_downloaded is not None:
            self.on_downloaded(filename)
        return local


def tick_after(filename: str, stops: list[DrivenStop], clock: Clock) -> Callable[[str], None]:
    """A download hook: once ``filename`` lands, move the clock and tick."""

    def hook(name: str) -> None:
        if name == filename and stops:
            clock.now = TICK_AT
            stops[-1].tick()

    return hook


def install_hub(monkeypatch: Any, client: Any) -> None:
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def new_archive(parent: Path) -> Path:
    archive = parent / "archive"
    archive.mkdir(parents=True)
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def reset_cli_logging() -> None:
    """Drop the handler bound to the previous invoke's (closed) stderr."""
    logging.getLogger("llm_preserver").handlers.clear()


def pull(archive: Path, *args: str, stdin: str | None = None) -> Any:
    reset_cli_logging()
    return runner.invoke(app, ["pull", REPO_ID, str(archive), *args], input=stdin)


def lines_of(result: Any) -> list[str]:
    """Interleaved stdout + stderr, unstyled, one entry per line (never doubled)."""
    return click.unstyle(result.output).splitlines()


def progress_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith(PREFIX)]


def ticker_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == TICKER_THREAD and t.is_alive()]


def model_dir(archive: Path) -> Path:
    return archive / "models" / "bartowski" / "tiny-chat-GGUF"
