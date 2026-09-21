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
