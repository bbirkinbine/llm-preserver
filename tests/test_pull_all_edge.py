"""Edge tests for pull --whole-repo (spec 0004): resume preflight, odd trees.

The disk preflight must not double-count bytes that already survived
an interrupted pull in staging (the client reuses them on rerun); a
docs-only tree is a legitimate snapshot (the tree is the artifact);
an empty repo names its emptiness instead of claiming "already
archived". FakeHubClient from conftest; no network.
"""

import shutil
from collections import namedtuple

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

SNAPSHOT_REPO_ID = "acme/tiny-orig"
SHARD_1 = "model-00001-of-00002.safetensors"
SHARD_2 = "model-00002-of-00002.safetensors"
SNAPSHOT_FILES = [
    ("config.json", b'{"architectures": ["TinyChat"]}', False),
    (SHARD_1, b"shard one bytes", True),
    (SHARD_2, b"shard two bytes", True),
    ("README.md", b"# tiny original\n", False),
]

DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def make_snapshot_client(fake_hub_factory, **overrides):
    overrides.setdefault("files", SNAPSHOT_FILES)
    overrides.setdefault("repo_id", SNAPSHOT_REPO_ID)
    return fake_hub_factory(**overrides)


def do_pull_all(archive_root, client, repo_id=SNAPSHOT_REPO_ID, **kwargs):
    kwargs.setdefault("include", ())
    kwargs.setdefault("model", "acme/tiny-chat")
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(archive_root, repo_id, client, select_all=True, **kwargs)


def model_dir(archive_root):
    return archive_root / "models" / "acme" / "tiny-chat"


def fake_disk_free(monkeypatch, free):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: DiskUsage(10**12, 10**12 - free, free))


def test_resume_preflight_discounts_bytes_already_in_staging(
    archive, fake_hub_factory, monkeypatch
):
    # An interrupted --whole-repo leaves fully downloaded files in staging;
    # the record was never written, so the rerun re-plans the whole
    # tree. The preflight must charge only the bytes still missing —
    # the client reuses the staged files.
    failing = make_snapshot_client(
        fake_hub_factory,
        download_error=hub.PullHubError("hub returned 500; retry later"),
        fail_after_downloads=2,
    )
    with pytest.raises(hub.PullHubError):
        do_pull_all(archive, failing)

    staging = archive / ".staging" / "acme" / "tiny-chat"
    staged_bytes = sum(
        p.stat().st_size for p in staging.rglob("*") if p.is_file() and ".cache" not in p.parts
    )
    assert staged_bytes > 0  # the interruption left completed files behind
    total = sum(len(content) for _path, content, _is_lfs in SNAPSHOT_FILES)
    # Free space fits only the remainder — a full-tree charge would refuse.
    fake_disk_free(monkeypatch, total - staged_bytes)

    do_pull_all(archive, make_snapshot_client(fake_hub_factory))

    for path, content, _is_lfs in SNAPSHOT_FILES:
        assert (model_dir(archive) / "hf-snapshot" / path).read_bytes() == content


def test_all_on_docs_only_repo_archives_the_tree(archive, fake_hub_factory):
    # Under --whole-repo the tree is the artifact: a repo holding only docs is
    # a legitimate snapshot, not the selective shape's docs-only error.
    client = fake_hub_factory(
        files=[("README.md", b"# docs only\n", False), ("LICENSE", b"MIT\n", False)],
        repo_id=SNAPSHOT_REPO_ID,
    )

    do_pull_all(archive, client)

    snapshot = model_dir(archive) / "hf-snapshot"
    assert (snapshot / "README.md").read_bytes() == b"# docs only\n"
    assert (snapshot / "LICENSE").read_bytes() == b"MIT\n"
    assert load_record(model_dir(archive)).artifacts[0].format == "hf-snapshot"


def test_all_on_empty_repo_names_the_emptiness(archive, fake_hub_factory):
    client = fake_hub_factory(files=[], repo_id=SNAPSHOT_REPO_ID)

    with pytest.raises(hub.PullUserError) as excinfo:
        do_pull_all(archive, client)

    message = str(excinfo.value)
    assert "no files" in message
    assert "already archived" not in message
    assert list((archive / "models").iterdir()) == []


def test_fresh_all_confirmation_shows_full_counts_without_already_clause(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull_all(archive, make_snapshot_client(fake_hub_factory), confirm=confirm)

    [prompt] = prompts
    assert "4 of 4" in prompt
    assert "already" not in prompt  # nothing is covered on a fresh pull


def test_resume_confirmation_shows_remaining_not_totals(archive, fake_hub_factory):
    # Plan -> preflight -> confirm (spec 0004 adjudications): the
    # confirmation states what will actually download on this run.
    do_pull_all(archive, make_snapshot_client(fake_hub_factory))
    target = model_dir(archive) / "hf-snapshot" / SHARD_2
    target.chmod(0o644)
    target.unlink()
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull_all(archive, make_snapshot_client(fake_hub_factory), confirm=confirm)

    [prompt] = prompts
    assert "1 of 4" in prompt  # only the missing shard downloads
    assert "3 already archived" in prompt
    assert "15 B" in prompt  # the missing shard's bytes, not the tree's
    assert target.read_bytes() == b"shard two bytes"
