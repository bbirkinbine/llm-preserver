"""Size confirmation + disk preflight on selective pulls (spec 0005).

Spec 0004 gave the whole-tree path a plan -> preflight -> confirm
sequence; the spec-0005 rider extends it to every path through
``pull_model``: a selective pull states the selection's total download
size in one confirmation composed by the same function as the
whole-repo one (so it starts with ``pull ``), the disk preflight
refuses an over-budget selective pull before anyone confirms, and
declining the size confirmation downloads nothing. FakeHubClient from
conftest; no network.
"""

import shutil
from collections import namedtuple

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive

REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
README_BYTES = b"# tiny-chat quantized\n"
HUB_FILES = [
    (Q4_NAME, Q4_BYTES, True),
    ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
    ("README.md", README_BYTES, False),
]
# *Q4_K_M* selects the Q4 weight; the README rides along as a doc, so
# the confirmed selection is two files totalling their byte lengths.
SELECTED_BYTES = len(Q4_BYTES) + len(README_BYTES)

DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def make_client(fake_hub_factory, **overrides):
    overrides.setdefault("files", HUB_FILES)
    return fake_hub_factory(**overrides)


def do_pull(archive_root, client, **kwargs):
    kwargs.setdefault("include", ["*Q4_K_M*"])
    kwargs.setdefault("model", "acme/tiny-chat")
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(archive_root, REPO_ID, client, **kwargs)


def fake_disk_free(monkeypatch, free):
    """Pin the preflight seam: pull sizes free space via shutil.disk_usage."""
    monkeypatch.setattr(shutil, "disk_usage", lambda path: DiskUsage(10**12, 10**12 - free, free))


def test_selective_pull_asks_size_confirmation_naming_total_and_repo(archive, fake_hub_factory):
    # --model given and only one of two weights selected: the size
    # confirmation is the ONLY prompt, composed by the same function as
    # the whole-repo one (count, human-readable total, repo id — never
    # filenames).
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull(archive, make_client(fake_hub_factory), confirm=confirm)

    [prompt] = prompts
    assert prompt.startswith("pull ")
    assert "2 of 2 files" in prompt
    assert f"{SELECTED_BYTES} B" in prompt
    assert REPO_ID in prompt
    assert Q4_NAME not in prompt  # counts and totals, never filenames


def test_selective_size_confirmation_is_asked_before_any_download(archive, fake_hub_factory):
    client = make_client(fake_hub_factory)
    downloads_at_confirm_time = []

    def confirm(prompt):
        if prompt.startswith("pull "):
            downloads_at_confirm_time.append(list(client.download_calls))
        return True

    do_pull(archive, client, confirm=confirm)

    assert downloads_at_confirm_time == [[]]


def test_declined_size_confirmation_downloads_and_writes_nothing(archive, fake_hub_factory):
    client = make_client(fake_hub_factory)

    with pytest.raises(hub.PullUserError):
        # Accept everything except the size confirmation.
        do_pull(archive, client, confirm=lambda prompt: not prompt.startswith("pull "))

    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_selective_pull_over_disk_budget_refuses_before_prompting(
    archive, fake_hub_factory, monkeypatch
):
    fake_disk_free(monkeypatch, 1)
    client = make_client(fake_hub_factory)
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    with pytest.raises(hub.PullEnvError) as excinfo:
        do_pull(archive, client, confirm=confirm)

    message = str(excinfo.value)
    assert f"{SELECTED_BYTES} B" in message  # required
    assert "1 B" in message  # available
    assert prompts == []  # plan -> preflight -> confirm: refusal comes first
    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_adopt_only_pull_asks_no_size_confirmation(archive, fake_hub_factory):
    # Reconcile-by-hash adoption moves zero bytes; asking to "pull 0
    # files (0 B)" would block scripted re-pulls for nothing
    # (spec 0005 adjudication, 2026-07-12). The record still updates.
    client = fake_hub_factory(files=[(Q4_NAME, Q4_BYTES, True)])
    target = archive / "models" / "acme" / "tiny-chat" / "gguf" / Q4_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(Q4_BYTES)
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull(archive, client, confirm=confirm)

    assert [prompt for prompt in prompts if prompt.startswith("pull ")] == []
    assert (archive / "models" / "acme" / "tiny-chat" / "model-record.json").is_file()


def test_fully_archived_selective_repull_asks_no_size_confirmation(archive, fake_hub_factory):
    # Mirrors the whole-repo shape: nothing to download means nothing
    # to confirm — an idempotent re-pull never asks "pull 0 files?".
    client = make_client(fake_hub_factory)
    do_pull(archive, client)
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull(archive, client, confirm=confirm)

    assert prompts == []
