"""Transfer-progress contract at the Hugging Face client boundary (spec 0026, pass 3).

``update_transfer(n)`` on a ``tqdm_class`` is a convention the client finds
with ``getattr``; if a release drops it nothing errors and progress silently
stops. The real-client tests notice: they drive ``HubClient`` through the
client's public ``set_client_factory`` hook and an ``httpx.MockTransport``, so
its real HEAD, GET, chunking, and local-dir reuse run offline, at the lock and
at the 1.26.0 floor. Headers follow ``get_hf_file_metadata`` (huggingface_hub
1.33.0 and 1.26.0 installed source, Apache-2.0, read 2026-11-06); no
``X-Xet-Hash`` header means the plain-HTTP path.
"""

import hashlib
import threading
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import tqdm.auto
from huggingface_hub import constants as hf_constants
from huggingface_hub import set_client_factory
from huggingface_hub.utils import _http as hf_http
from huggingface_hub.utils.tqdm import tqdm as hf_tqdm
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from tqdm._monitor import TMonitor

import llm_preserver.hub as hub
from llm_preserver.hub import client as hub_client

PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"
REPO_ID = "acme/progress-probe"
FILENAME = "weights/probe.gguf"
COMMIT = "c" * 40
TODAYS_CALL = {"repo_id": REPO_ID, "filename": FILENAME, "revision": COMMIT}

# Every keyword http_get and the Xet reporter pass a bar class, plus ``name``
# and an invented one: the bridge must survive a release adding more.
HF_BAR_KWARGS: dict[str, object] = {
    "unit": "B",
    "unit_scale": True,
    "total": 1000,
    "initial": 0,
    "desc": FILENAME,
    "position": 1,
    "bar_format": "{desc}: {n_fmt}",
    "leave": True,
    "name": "huggingface_hub.http_get",
    "a_keyword_a_future_release_adds": object(),
}


class RecordingSink:
    """A ``TransferSink`` that records every increment, or fails on each one."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[int] = []
        self.fail = fail

    def add_transfer_bytes(self, n: int) -> None:
        if self.fail:
            raise RuntimeError("sink failure")
        self.calls.append(n)


# Any: the bridge's surface is duck-typed (``update_transfer`` and friends are
# found by the client with ``getattr``), which is exactly what these tests probe.
def _bridge_class(sink: object) -> Any:
    """Import lazily so each test fails on its own until pass 3 lands."""
    from llm_preserver.hub.progress_bridge import transfer_tqdm_class

    return transfer_tqdm_class(sink)


def _bar(sink: object) -> Any:
    return _bridge_class(sink)(**HF_BAR_KWARGS)


def _record_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def record_download(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(tmp_path / FILENAME)

    monkeypatch.setattr(hub_client, "HfApi", lambda: object())
    monkeypatch.setattr(hub_client, "hf_hub_download", record_download)
    return calls


def test_download_without_a_sink_makes_todays_exact_client_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 4: no sink means no ``tqdm_class`` key reaches the client."""
    calls = _record_downloads(monkeypatch, tmp_path)

    hub.HubClient().download(REPO_ID, FILENAME, COMMIT, tmp_path, progress=None)

    assert calls == [{**TODAYS_CALL, "local_dir": tmp_path}]


def test_download_with_a_sink_adds_exactly_one_tqdm_class_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sink changes the client call by one key and nothing else."""
    calls = _record_downloads(monkeypatch, tmp_path)

    hub.HubClient().download(REPO_ID, FILENAME, COMMIT, tmp_path, progress=RecordingSink())

    assert len(calls) == 1
    passed = dict(calls[0])
    assert isinstance(passed.pop("tqdm_class"), type)
    assert passed == {**TODAYS_CALL, "local_dir": tmp_path}


def test_download_with_a_sink_passes_a_class_that_reports_to_that_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The class handed to the client is wired to the caller's own sink."""
    calls = _record_downloads(monkeypatch, tmp_path)
    sink = RecordingSink()

    hub.HubClient().download(REPO_ID, FILENAME, COMMIT, tmp_path, progress=sink)
    calls[0]["tqdm_class"](**HF_BAR_KWARGS).update_transfer(7)

    assert sink.calls == [7]


def test_transfer_sink_protocol_is_exported_from_the_hub_seam() -> None:
    """Core code types its meter against ``llm_preserver.hub.TransferSink``."""
    assert "TransferSink" in hub.__all__
    assert callable(getattr(hub.TransferSink, "add_transfer_bytes", None))


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


class FakeResolveEndpoint:
    """Serve one file's HEAD metadata and GET body; refuse anything else."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.method)
        if request.url.path != f"/{REPO_ID}/resolve/{COMMIT}/{FILENAME}":
            return httpx.Response(404, headers={"X-Error-Code": "EntryNotFound"})
        headers = {
            hf_constants.HUGGINGFACE_HEADER_X_REPO_COMMIT: COMMIT,
            "ETag": f'"{hashlib.sha256(self.body).hexdigest()}"',
            hf_constants.HUGGINGFACE_HEADER_X_LINKED_SIZE: str(len(self.body)),
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        if request.method == "GET":
            return httpx.Response(200, headers=headers, content=self.body)
        return httpx.Response(405)


@pytest.fixture
def offline_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeResolveEndpoint]:
    """Route the real client's HTTP through a mock transport, plain-HTTP path only."""
    # Two chunks at the client's own chunk size, so increments arrive more than once.
    endpoint = FakeResolveEndpoint(b"\x5a" * (hf_constants.DOWNLOAD_CHUNK_SIZE + 4096))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    # The client reads these module constants at call time (set from env at import).
    monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", str(tmp_path / "hf-home" / "hub"))
    monkeypatch.setattr(hf_constants, "HF_HUB_DISABLE_XET", True)
    monkeypatch.setattr(hf_constants, "HF_HUB_DISABLE_IMPLICIT_TOKEN", True)
    # Private read: restore whatever factory was installed, not an assumed default.
    previous = hf_http._GLOBAL_CLIENT_FACTORY
    set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(endpoint)))
    try:
        yield endpoint
    finally:
        set_client_factory(previous)


def _real_download(staging: Path, sink: RecordingSink) -> Path:
    return hub.HubClient().download(REPO_ID, FILENAME, COMMIT, staging, progress=sink)


def test_real_client_reports_every_plain_http_body_byte_through_the_sink(
    offline_hub: FakeResolveEndpoint, tmp_path: Path
) -> None:
    """The installed client still calls ``update_transfer`` per chunk."""
    sink = RecordingSink()

    downloaded = _real_download(tmp_path / "staging", sink)

    assert downloaded.read_bytes() == offline_hub.body
    assert sum(sink.calls) == len(offline_hub.body)
    assert len(sink.calls) >= 2


def test_real_client_renders_no_bar_when_given_a_sink(
    offline_hub: FakeResolveEndpoint, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Criterion 5: the plain lines are the only progress shown."""
    _real_download(tmp_path / "staging", RecordingSink())

    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_real_client_reuse_of_a_staged_file_moves_zero_transfer_bytes(
    offline_hub: FakeResolveEndpoint, tmp_path: Path
) -> None:
    """Zero bytes on a staged file is the reuse signal pass 4 cross-checks."""
    staging = tmp_path / "staging"
    _real_download(staging, RecordingSink())
    gets_before = offline_hub.requests.count("GET")
    second = RecordingSink()

    _real_download(staging, second)

    assert sum(second.calls) == 0
    assert offline_hub.requests.count("GET") == gets_before


def test_tqdm_is_a_declared_direct_runtime_dependency() -> None:
    """The bridge imports tqdm itself, so it must not ride a transitive dependency."""
    project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]
    names = {canonicalize_name(Requirement(raw).name) for raw in project["dependencies"]}

    assert "tqdm" in names
