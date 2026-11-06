"""The local HF cache probe at the hub seam (spec 0026, criterion 3, review round).

``hf_hub_download(local_dir=...)`` copies a file from the user's Hugging Face
cache when ``try_to_load_from_cache`` finds it there, moving zero network
bytes (installed 1.33.0 source, ``_hf_hub_download_to_local_dir``,
Apache-2.0, read 2026-11-06). ``HubClient.cached_locally`` makes the same
lookup before the download so the meter can name a multi-GiB local copy
instead of reading it as a stall. The unit tests patch the lookup where
``llm_preserver.hub.client`` imports it (the ``test_hf_client_compatibility``
style); the real-client test populates an isolated cache through the
offline hub and asks the real lookup.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from hf_offline_shapes import COMMIT, FILENAME, REPO_ID, FakeResolveEndpoint, install_offline_hub
from huggingface_hub import _CACHED_NO_EXIST, hf_hub_download

import llm_preserver.hub as hub
from llm_preserver.hub import client as hub_client
from llm_preserver.hub.types import HubClientProtocol


def _patch_lookup(monkeypatch: pytest.MonkeyPatch, lookup: Callable[..., object]) -> None:
    monkeypatch.setattr(hub_client, "HfApi", lambda: object())
    # raising=False: the name does not exist in the module until the probe lands.
    monkeypatch.setattr(hub_client, "try_to_load_from_cache", lookup, raising=False)


def test_hub_protocol_declares_the_cache_probe() -> None:
    assert callable(getattr(HubClientProtocol, "cached_locally", None))


def test_cached_locally_is_true_when_the_lookup_returns_a_cached_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_lookup(monkeypatch, lambda **_: "/cache/snapshots/c/weights/probe.gguf")

    assert hub.HubClient().cached_locally(REPO_ID, FILENAME, COMMIT) is True


@pytest.mark.parametrize("answer", [None, _CACHED_NO_EXIST], ids=["not-cached", "no-exist"])
def test_cached_locally_is_false_unless_the_lookup_returns_a_path(
    monkeypatch: pytest.MonkeyPatch, answer: object
) -> None:
    """``_CACHED_NO_EXIST`` is a truthy sentinel: only a ``str`` means cached."""
    _patch_lookup(monkeypatch, lambda **_: answer)

    assert hub.HubClient().cached_locally(REPO_ID, FILENAME, COMMIT) is False


@pytest.mark.parametrize("error", [OSError("stale handle"), ValueError("bad"), RuntimeError("x")])
def test_cached_locally_is_false_and_never_raises_when_the_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """A probe for a progress label must never fail the pull it labels."""

    def failing_lookup(**_: object) -> object:
        raise error

    _patch_lookup(monkeypatch, failing_lookup)

    assert hub.HubClient().cached_locally(REPO_ID, FILENAME, COMMIT) is False


def test_cached_locally_asks_for_that_file_at_that_pinned_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same lookup the client makes: repo, file, and the commit, never a branch."""
    calls: list[dict[str, object]] = []

    def recording_lookup(**kwargs: object) -> object:
        calls.append(kwargs)
        return None

    _patch_lookup(monkeypatch, recording_lookup)

    hub.HubClient().cached_locally(REPO_ID, FILENAME, COMMIT)

    assert calls == [{"repo_id": REPO_ID, "filename": FILENAME, "revision": COMMIT}]


@pytest.fixture
def offline_cache_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeResolveEndpoint]:
    """The offline hub with an isolated, initially empty HF cache under ``tmp_path``."""
    endpoint = FakeResolveEndpoint(b"\x5a" * 4096)
    with install_offline_hub(tmp_path, monkeypatch, endpoint):
        yield endpoint


def test_real_client_cache_probe_finds_a_file_a_cache_mode_download_left(
    offline_cache_hub: FakeResolveEndpoint,
) -> None:
    """Populate the cache the way any other HF tool would, then ask the real lookup."""
    client = hub.HubClient()
    assert client.cached_locally(REPO_ID, FILENAME, COMMIT) is False

    hf_hub_download(repo_id=REPO_ID, filename=FILENAME, revision=COMMIT)

    assert client.cached_locally(REPO_ID, FILENAME, COMMIT) is True
    assert client.cached_locally(REPO_ID, "weights/other.gguf", COMMIT) is False
    assert client.cached_locally(REPO_ID, FILENAME, "d" * 40) is False
