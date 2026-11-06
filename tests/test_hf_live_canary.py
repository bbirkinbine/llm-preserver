"""Opt-in live qualification of the public Hugging Face Hub seam.

Ordinary pytest runs skip this module. Set ``LLM_PRESERVER_RUN_HF_LIVE=1``
in an anonymous environment to exercise current hosted behavior.
"""

import json
import os
from pathlib import Path

import pytest

from llm_preserver.hub import HubClient

# Source: https://huggingface.co/openai-community/gpt2/blob/607a30d783dfa663caf39e06633721c8d4cfcd7e/config.json
# Retrieved 2026-09-21; the openai-community/gpt2 model card declares MIT.
CANARY_REPO_ID = "openai-community/gpt2"
CANARY_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"
CANARY_FILENAME = "config.json"
CANARY_SIZE = 665

pytestmark = pytest.mark.skipif(
    os.environ.get("LLM_PRESERVER_RUN_HF_LIVE") != "1",
    reason="live Hugging Face canary is opt-in",
)


def test_live_metadata_and_discovery_for_public_gpt2() -> None:
    """Current Hub metadata, summary, and exact-repo search cross the real seam."""
    client = HubClient()

    repo = client.repo_info(CANARY_REPO_ID)
    summary = client.model_summary(CANARY_REPO_ID)
    search_page = client.search_models(CANARY_REPO_ID).next_page()

    assert len(repo.commit) == 40
    assert any(file.path == CANARY_FILENAME and file.size == CANARY_SIZE for file in repo.files)
    assert summary.repo_id == CANARY_REPO_ID
    assert CANARY_REPO_ID in {result.repo_id for result in search_page}


def test_live_download_of_pinned_gpt2_config(tmp_path: Path) -> None:
    """Pinned local_dir download returns the expected tiny public config."""
    downloaded = HubClient().download(
        CANARY_REPO_ID,
        CANARY_FILENAME,
        CANARY_REVISION,
        tmp_path,
    )

    content = downloaded.read_bytes()
    assert downloaded == tmp_path / CANARY_FILENAME
    assert len(content) == CANARY_SIZE
    assert json.loads(content)["model_type"] == "gpt2"


def test_live_revision_request_resolves_to_the_pinned_commit() -> None:
    """A full-SHA ``revision`` reaches the hub and comes back as that commit (spec 0024).

    Read-only metadata; pins a commit, never a moving branch head.
    """
    repo = HubClient().repo_info(CANARY_REPO_ID, revision=CANARY_REVISION)

    assert repo.commit == CANARY_REVISION
    assert any(file.path == CANARY_FILENAME and file.size == CANARY_SIZE for file in repo.files)


# Source: https://huggingface.co/unsloth/SmolLM2-135M-Instruct-GGUF/tree/9e6855bc4be717fca1ef21360a1db4b29d5c559a
# Retrieved 2026-11-06 through the Hub API (model_info + file metadata:
# Xet-backed, size and LFS sha256 below); the model card declares
# apache-2.0. The smallest Xet-backed file found for a weekly download.
XET_CANARY_REPO_ID = "unsloth/SmolLM2-135M-Instruct-GGUF"
XET_CANARY_REVISION = "9e6855bc4be717fca1ef21360a1db4b29d5c559a"
XET_CANARY_FILENAME = "SmolLM2-135M-Instruct-Q2_K.gguf"
XET_CANARY_SIZE = 88201792
XET_CANARY_SHA256 = "c53fe6626c7165ebfd8de5db22edc3f719b813da001e662bc5cb453f2540a076"


class _CountingSink:
    def __init__(self) -> None:
        self.increments: list[int] = []

    def add_transfer_bytes(self, n: int) -> None:
        self.increments.append(n)


def test_live_xet_download_reports_progress_through_the_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Xet-backed download feeds ``pull --progress``'s sink (spec 0026).

    The offline tests drive the client's Xet progress reporter with fake
    reports; only a real Xet transfer proves hf_xet still calls it. Two
    caches could serve the bytes without a transfer, so both are fresh:
    the hub cache (``HF_HUB_CACHE``, which ``hf_hub_download`` copies
    from in local-dir mode, read at call time) and hf_xet's chunk cache
    (``HF_XET_CACHE``, read from the environment). A spy on ``xet_get``
    proves the Xet path ran: over plain HTTP the same sink assertions
    would pass on the wrong path.
    """
    import huggingface_hub.constants as hf_constants
    import huggingface_hub.file_download as file_download

    from llm_preserver.hashing import sha256_of

    monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", str(tmp_path / "hub-cache"))
    monkeypatch.setenv("HF_XET_CACHE", str(tmp_path / "xet-cache"))
    xet_calls: list[str] = []
    real_xet_get = file_download.xet_get

    def spy_xet_get(*args: object, **kwargs: object) -> object:
        xet_calls.append("xet_get")
        return real_xet_get(*args, **kwargs)

    monkeypatch.setattr(file_download, "xet_get", spy_xet_get)
    sink = _CountingSink()

    downloaded = HubClient().download(
        XET_CANARY_REPO_ID,
        XET_CANARY_FILENAME,
        XET_CANARY_REVISION,
        tmp_path / "staging",
        progress=sink,
    )

    assert downloaded.stat().st_size == XET_CANARY_SIZE
    assert sha256_of(downloaded) == XET_CANARY_SHA256
    assert xet_calls == ["xet_get"]
    assert len(sink.increments) >= 2
    assert sum(sink.increments) > XET_CANARY_SIZE // 2
