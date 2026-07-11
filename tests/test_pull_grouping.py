"""Tests for format-directed grouping (spec 0004 adjudications).

The three cells: base_model absent → repo-id default (any format);
base_model present + GGUF/MLX tree → group under the base (conversions,
0003 behavior); base_model present + hf-snapshot tree → the repo is its
own home and the base is lineage only (derived models — the live
mis-grouping this rule fixes). FakeHubClient from conftest; no network.
"""

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive

GGUF_REPO_ID = "bartowski/tiny-chat-GGUF"
GGUF_FILES = [
    ("tiny-chat-Q4_K_M.gguf", b"q4 weight bytes", True),
    ("README.md", b"# quantized\n", False),
]
SNAPSHOT_REPO_ID = "acme/tiny-chat-instruct"
SNAPSHOT_FILES = [
    ("config.json", b"{}", False),
    ("model.safetensors", b"derived weights", True),
    ("README.md", b"# derived\n", False),
]
BASE_MODEL = "acme/tiny-chat-base"


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def pull_with_prompts(archive_root, client, repo_id, include):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    pull.pull_model(archive_root, repo_id, client, include=include, model=None, confirm=confirm)
    return prompts


def test_no_base_model_defaults_to_repo_id_for_gguf_trees(archive, fake_hub_factory):
    client = fake_hub_factory(files=GGUF_FILES, repo_id=GGUF_REPO_ID, base_model=None)

    prompts = pull_with_prompts(archive, client, GGUF_REPO_ID, ["*Q4_K_M*"])

    assert any(GGUF_REPO_ID in prompt for prompt in prompts)
    assert (archive / "models" / "bartowski" / "tiny-chat-GGUF").is_dir()


def test_no_base_model_defaults_to_repo_id_for_snapshot_trees(archive, fake_hub_factory):
    client = fake_hub_factory(files=SNAPSHOT_FILES, repo_id=SNAPSHOT_REPO_ID, base_model=None)

    prompts = pull_with_prompts(archive, client, SNAPSHOT_REPO_ID, ["*.safetensors"])

    assert any(SNAPSHOT_REPO_ID in prompt for prompt in prompts)
    assert (archive / "models" / "acme" / "tiny-chat-instruct").is_dir()


def test_gguf_tree_with_base_model_groups_under_the_base(archive, fake_hub_factory):
    # Conversions: same weights, different container (0003 behavior).
    client = fake_hub_factory(files=GGUF_FILES, repo_id=GGUF_REPO_ID, base_model=BASE_MODEL)

    prompts = pull_with_prompts(archive, client, GGUF_REPO_ID, ["*Q4_K_M*"])

    assert any(BASE_MODEL in prompt for prompt in prompts)
    assert (archive / "models" / "acme" / "tiny-chat-base").is_dir()
    assert not (archive / "models" / "bartowski").exists()


def test_snapshot_tree_with_base_model_is_its_own_home_with_lineage(archive, fake_hub_factory):
    # Derived models: different weights — base_model is lineage, never
    # the home (the live Qwen3-0.6B-instruct-under-Base mis-grouping).
    client = fake_hub_factory(files=SNAPSHOT_FILES, repo_id=SNAPSHOT_REPO_ID, base_model=BASE_MODEL)

    prompts = pull_with_prompts(archive, client, SNAPSHOT_REPO_ID, ["*.safetensors"])

    lineage_prompt = next(prompt for prompt in prompts if "lineage" in prompt)
    assert BASE_MODEL in lineage_prompt  # mentioned as lineage...
    assert SNAPSHOT_REPO_ID in lineage_prompt  # ...while the repo is the home
    assert (archive / "models" / "acme" / "tiny-chat-instruct").is_dir()
    assert not (archive / "models" / "acme" / "tiny-chat-base").exists()


def test_declined_snapshot_own_home_names_model_flag(archive, fake_hub_factory):
    client = fake_hub_factory(files=SNAPSHOT_FILES, repo_id=SNAPSHOT_REPO_ID, base_model=BASE_MODEL)

    with pytest.raises(hub.PullUserError) as excinfo:
        pull.pull_model(
            archive,
            SNAPSHOT_REPO_ID,
            client,
            include=["*.safetensors"],
            model=None,
            confirm=lambda prompt: False,
        )

    assert "--model" in str(excinfo.value)
    assert list((archive / "models").iterdir()) == []
