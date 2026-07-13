"""Rename-resolution of the declared base model (2026-07-13).

A repo's card can declare a parent by a pre-rename name the hub now
redirects; one light metadata call resolves it so the grouping
proposal, the mismatch warning, and the master advisory all speak
the hub's current name — the live confusion this fixes: the discover
tree showed the current name while the pull proposed the stale one.
Unresolvable or malformed declared bases fall back to the declared
name and never abort a pull. FakeHubClient from conftest; no network.
"""

import pytest

import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.hub_discovery import ModelSummary

REPO_ID = "bartowski/tiny-chat-GGUF"
HUB_FILES = [
    ("tiny-chat-Q4_K_M.gguf", b"q4 weight bytes", True),
    ("README.md", b"# tiny-chat quantized\n", False),
]


def summary(repo_id, **overrides):
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def do_pull(archive_root, client, confirm):
    return pull.pull_model(
        archive_root,
        REPO_ID,
        client,
        include=["*Q4_K_M*"],
        confirm=confirm,
    )


def test_renamed_base_resolves_to_current_id_everywhere(archive, fake_hub_factory, caplog):
    # Card declares old/base; the hub redirects it to new/base. The
    # grouping proposal and the record home must use the current id.
    import logging

    caplog.set_level(logging.INFO, logger="llm_preserver")
    client = fake_hub_factory(
        files=HUB_FILES,
        base_model="old/base",
        summaries={"old/base": summary("new/base")},
    )
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    model_dir = do_pull(archive, client, confirm)

    assert model_dir == archive / "models" / "new" / "base"
    grouping = next(prompt for prompt in prompts if "canonical model" in prompt)
    assert "new/base" in grouping
    assert "old/base" not in grouping
    assert client.model_summary_calls == ["old/base"]
    # The resolution is disclosed, not silent.
    assert any("renamed on the hub" in record.getMessage() for record in caplog.records)


def test_unresolvable_base_falls_back_to_the_declared_name(archive, fake_hub_factory):
    # No summary configured -> the fake 404s -> declared name stands
    # (the pre-existing behavior; resolution never aborts a pull).
    client = fake_hub_factory(files=HUB_FILES, base_model="acme/tiny-chat")

    model_dir = do_pull(archive, client, confirm=lambda prompt: True)

    assert model_dir == archive / "models" / "acme" / "tiny-chat"
    assert client.model_summary_calls == ["acme/tiny-chat"]


def test_malformed_declared_base_is_never_looked_up(archive, fake_hub_factory):
    # The traversal-shaped string must not become a request parameter;
    # downstream, present-but-unusable metadata stays the 0003 hard
    # stop at grouping (this is a GGUF conversion, so the declared
    # base is the proposed home).
    from llm_preserver.hub import PullUserError

    client = fake_hub_factory(files=HUB_FILES, base_model="../../evil?x=")

    with pytest.raises(PullUserError):
        do_pull(archive, client, confirm=lambda prompt: True)

    assert client.model_summary_calls == []


def test_no_declared_base_makes_no_resolution_call(archive, fake_hub_factory):
    client = fake_hub_factory(files=HUB_FILES, base_model=None)

    do_pull(archive, client, confirm=lambda prompt: True)

    assert client.model_summary_calls == []
