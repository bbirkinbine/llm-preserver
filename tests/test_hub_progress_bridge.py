"""Unit tests for the ``tqdm_class`` progress bridge (spec 0026).

``transfer_tqdm_class`` builds the class handed to ``hf_hub_download``.
These tests probe the class directly; the tests that drive the real
client's HTTP path live in ``test_hf_client_progress_contract.py``.
The Xet test drives the installed client's own Xet progress reporter
offline, since hf_xet itself needs the network: it reads a private
module (``utils._xet_progress_reporting``), which a release may move,
so it runs in the compatibility matrix at the floor and the lock.
"""

import logging
import threading
from types import SimpleNamespace
from typing import Any

import pytest
import tqdm.auto
from hf_offline_shapes import HF_BAR_KWARGS, RecordingSink
from huggingface_hub.utils._xet_progress_reporting import XetDownloadProgressReporter
from huggingface_hub.utils.tqdm import tqdm as hf_tqdm
from tqdm._monitor import TMonitor

BRIDGE_LOGGER = "llm_preserver.hub.progress_bridge"


# Any: the bridge's surface is duck-typed (``update_transfer`` and friends are
# found by the client with ``getattr``), which is exactly what these tests probe.
def _bridge_class(sink: object) -> Any:
    from llm_preserver.hub.progress_bridge import transfer_tqdm_class

    return transfer_tqdm_class(sink)


def _bar(sink: object) -> Any:
    return _bridge_class(sink)(**HF_BAR_KWARGS)


def test_bridge_is_vanilla_tqdm_and_not_the_clients_own_bar() -> None:
    """Criterion 5: the client's subclass would keep rendering its bar on a TTY."""
    bar_class = _bridge_class(RecordingSink())

    assert issubclass(bar_class, tqdm.auto.tqdm)
    assert not issubclass(bar_class, hf_tqdm)


def test_bridge_accepts_every_keyword_the_client_passes() -> None:
    """Vanilla tqdm rejects unknown keywords; the bridge must not."""
    assert _bar(RecordingSink()) is not None


def test_bridge_bar_is_disabled() -> None:
    """No bar renders, on a TTY or off one."""
    assert _bar(RecordingSink()).disable is True


def test_update_transfer_forwards_signed_increments_to_the_sink() -> None:
    """Positive chunks and the negative rewind of a ranged retry both pass through."""
    sink = RecordingSink()
    bar = _bar(sink)

    bar.update_transfer(10)
    bar.update_transfer(-4)

    assert sink.calls == [10, -4]


def test_update_does_not_count_as_network_bytes() -> None:
    """``update`` tracks bytes written to disk, which Xet can exceed."""
    sink = RecordingSink()

    _bar(sink).update(100)

    assert sink.calls == []


def test_update_transfer_never_raises_when_the_sink_raises() -> None:
    """Criterion 6: a measuring failure must not fail the download it rides in."""
    bar = _bar(RecordingSink(fail=True))

    assert bar.update_transfer(10) is None


def test_a_sink_failure_is_swallowed_but_logged_once_at_debug_with_the_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Swallowed, not silent: a broken meter must be findable with ``--verbose``."""
    caplog.set_level(logging.DEBUG, logger=BRIDGE_LOGGER)
    bar = _bar(RecordingSink(fail=True))

    assert bar.update_transfer(10) is None

    records = [r for r in caplog.records if r.name == BRIDGE_LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is RuntimeError


def test_set_transfer_postfix_str_is_a_no_op() -> None:
    """The Xet reporter calls it beside ``update_transfer``; it must exist."""
    bar = _bar(RecordingSink())

    assert bar.set_transfer_postfix_str("1.0MB/s", refresh=False) is None


def test_constructing_the_bridge_starts_no_tqdm_monitor_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tqdm starts its monitor in ``__new__``, even for a disabled bar."""
    bar_class = _bridge_class(RecordingSink())
    # Clear any monitor an earlier test left, so a fresh one would be created.
    for klass in bar_class.__mro__:
        if "monitor" in vars(klass):
            monkeypatch.setattr(klass, "monitor", None)
    before = {t for t in threading.enumerate() if isinstance(t, TMonitor)}

    bar_class(**HF_BAR_KWARGS)

    started = {t for t in threading.enumerate() if isinstance(t, TMonitor)} - before
    for monitor in started:
        monitor.exit()
    assert started == set()


def _group_report(*, written: int, received: int) -> SimpleNamespace:
    """The cumulative fields ``update_progress`` reads off hf_xet's group report."""
    return SimpleNamespace(
        total_bytes=1000,
        total_bytes_completed=written,
        total_bytes_completion_rate=None,
        total_transfer_bytes_completed=received,
        total_transfer_bytes_completion_rate=None,
    )


def test_xet_reporter_forwards_transfer_increments_through_the_bridge(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Xet's offline coverage: the reporter finds ``update_transfer`` and feeds the sink.

    Constructed the way ``xet_get`` does (installed 1.33.0 and 1.26.0 source,
    ``file_download.py``), minus the external bar a snapshot download passes.
    Reports are cumulative; the reporter turns them into increments.
    """
    sink = RecordingSink()
    reporter = XetDownloadProgressReporter(
        reconstruction_desc="weights/probe.gguf: reconstructing file",
        transfer_desc="weights/probe.gguf: downloading bytes",
        total=1000,
        log_level=logging.INFO,
        name="huggingface_hub.xet_get",
        tqdm_class=_bridge_class(sink),
    )

    reporter.update_progress(_group_report(written=0, received=300))
    reporter.update_progress(_group_report(written=1000, received=700))
    reporter.close()

    assert sink.calls == [300, 400]
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")
