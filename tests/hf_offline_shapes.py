"""Shared shapes for the offline Hugging Face client tests (spec 0026).

Plain helpers, no fixtures, like ``staging_shapes.py``. The real-client
tests drive the installed ``huggingface_hub`` through its public
``set_client_factory`` hook and an ``httpx.MockTransport``, so its real
HEAD, GET, chunking, local-dir reuse and cache lookup run offline, at
the lock and at the 1.26.0 floor. Headers follow ``get_hf_file_metadata``
(huggingface_hub 1.33.0 and 1.26.0 installed source, Apache-2.0, read
2026-11-06); no ``X-Xet-Hash`` header means the plain-HTTP path.

``install_offline_hub`` pins every client constant the request path
reads at call time, including the endpoint: ``constants.ENDPOINT`` and
``HUGGINGFACE_CO_URL_TEMPLATE`` are computed from ``HF_ENDPOINT`` (and
``HUGGINGFACE_CO_STAGING``) at import, so an inherited mirror endpoint
would otherwise send every request to a path the fake does not serve.
"""

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from huggingface_hub import constants as hf_constants
from huggingface_hub import set_client_factory
from huggingface_hub.utils import _http as hf_http

REPO_ID = "acme/progress-probe"
FILENAME = "weights/probe.gguf"
COMMIT = "c" * 40
PINNED_ENDPOINT = "https://huggingface.co"

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


def two_chunk_body() -> bytes:
    """Two chunks at the client's own chunk size, so increments arrive more than once."""
    return b"\x5a" * (hf_constants.DOWNLOAD_CHUNK_SIZE + 4096)


@contextmanager
def install_offline_hub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> Iterator[None]:
    """Route the real client through ``handler``, plain-HTTP path, isolated cache."""
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    # The client reads these module constants at call time (set from env at import).
    monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", str(tmp_path / "hf-home" / "hub"))
    monkeypatch.setattr(hf_constants, "HF_HUB_DISABLE_XET", True)
    monkeypatch.setattr(hf_constants, "HF_HUB_DISABLE_IMPLICIT_TOKEN", True)
    monkeypatch.setattr(hf_constants, "ENDPOINT", PINNED_ENDPOINT)
    monkeypatch.setattr(
        hf_constants,
        "HUGGINGFACE_CO_URL_TEMPLATE",
        PINNED_ENDPOINT + "/{repo_id}/resolve/{revision}/{filename}",
    )
    # Private read: restore whatever factory was installed, not an assumed default.
    previous = hf_http._GLOBAL_CLIENT_FACTORY
    set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        yield
    finally:
        set_client_factory(previous)
