"""Transfer-progress contract at the Hugging Face client boundary (spec 0026, pass 3).

``update_transfer(n)`` on a ``tqdm_class`` is a convention the client finds
with ``getattr``; if a release drops it nothing errors and progress silently
stops. The real-client tests notice: they drive ``HubClient`` through the
offline hub in ``hf_offline_shapes``, at the lock and at the 1.26.0 floor.
The bridge class's own unit tests live in ``test_hub_progress_bridge.py``.
"""

import os
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hf_offline_shapes import (
    COMMIT,
    FILENAME,
    HF_BAR_KWARGS,
    REPO_ID,
    FakeResolveEndpoint,
    RecordingSink,
    install_offline_hub,
    two_chunk_body,
)
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import llm_preserver.hub as hub
from llm_preserver.hub import client as hub_client

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
TODAYS_CALL = {"repo_id": REPO_ID, "filename": FILENAME, "revision": COMMIT}
MIRROR_ENDPOINT = "https://mirror.example/sub"


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


@pytest.fixture
def offline_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeResolveEndpoint]:
    """Route the real client's HTTP through a mock transport, plain-HTTP path only."""
    endpoint = FakeResolveEndpoint(two_chunk_body())
    with install_offline_hub(tmp_path, monkeypatch, endpoint):
        yield endpoint


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


def test_offline_contracts_pass_with_a_mirror_hf_endpoint_in_the_environment() -> None:
    """The client reads ``HF_ENDPOINT`` at import, so only a fresh interpreter proves it.

    A developer or CI runner exporting a mirror must not turn the offline
    contracts into 404s against a path the fake does not serve.
    """
    env = {**os.environ, "HF_ENDPOINT": MIRROR_ENDPOINT}
    files = ["tests/test_hf_client_progress_contract.py", "tests/test_hf_client_cache_probe.py"]
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", "real_client"]
    # S603: argv is this interpreter plus literals and two checked-in test paths.
    result = subprocess.run(  # noqa: S603
        [*argv, *files],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert " passed" in result.stdout


def test_tqdm_is_a_declared_direct_runtime_dependency() -> None:
    """The bridge imports tqdm itself, so it must not ride a transitive dependency."""
    project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]
    names = {canonicalize_name(Requirement(raw).name) for raw in project["dependencies"]}

    assert "tqdm" in names
