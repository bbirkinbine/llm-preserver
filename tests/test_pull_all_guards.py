"""Guard tests for pull --all (spec 0004): preflight, grouping, refresh.

The disk-space preflight refuses (local-environment domain) before any
byte downloads; grouping defaults to the repo id when the repo declares
no ``base_model`` (ratified 2026-07-11: applies to pull generally);
``--refresh-docs`` and idempotent re-runs keep their 0003 semantics on
the whole-tree shape; every download logs an ``n of m`` INFO counter
(ratified: on all pulls). FakeHubClient from conftest; no network.
"""

import hashlib
import logging
import shutil
from collections import namedtuple

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

FULL_COMMIT_HASH = "a" * 40  # FakeHubClient's default resolved commit
SNAPSHOT_REPO_ID = "acme/tiny-orig"
SHARD_1 = "model-00001-of-00002.safetensors"
SHARD_2 = "model-00002-of-00002.safetensors"
README_BYTES = b"# tiny original\n"
SNAPSHOT_FILES = [
    ("config.json", b'{"architectures": ["TinyChat"]}', False),
    (SHARD_1, b"shard one bytes", True),
    (SHARD_2, b"shard two bytes", True),
    ("README.md", README_BYTES, False),
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
    """Pin the preflight seam: pull sizes free space via shutil.disk_usage."""
    monkeypatch.setattr(shutil, "disk_usage", lambda path: DiskUsage(10**12, 10**12 - free, free))


def test_insufficient_disk_space_refuses_before_any_download(
    archive, fake_hub_factory, monkeypatch
):
    fake_disk_free(monkeypatch, 1)
    client = make_snapshot_client(fake_hub_factory)
    total = sum(len(content) for _path, content, _is_lfs in SNAPSHOT_FILES)

    with pytest.raises(hub.PullEnvError) as excinfo:
        do_pull_all(archive, client)

    message = str(excinfo.value)
    assert f"{total} B" in message  # required
    assert "1 B" in message  # available
    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_sufficient_disk_space_proceeds_to_download(archive, fake_hub_factory, monkeypatch):
    fake_disk_free(monkeypatch, 10**9)

    do_pull_all(archive, make_snapshot_client(fake_hub_factory))

    assert (model_dir(archive) / "hf-snapshot" / SHARD_1).is_file()


def test_files_without_hub_size_count_in_confirmation_but_not_sum(archive, fake_hub_factory):
    weight = b"x" * 100
    client = fake_hub_factory(
        files=[("model.safetensors", weight, True), ("tokenizer.json", b"tok", False)],
        repo_id=SNAPSHOT_REPO_ID,
    )
    info = hub.RepoInfo(
        commit=FULL_COMMIT_HASH,
        files=[
            hub.RepoFile(
                path="model.safetensors", size=100, sha256=hashlib.sha256(weight).hexdigest()
            ),
            hub.RepoFile(path="tokenizer.json", size=None, sha256=None),
        ],
        base_model="acme/tiny-chat",
        pipeline_tag=None,
        license=None,
    )
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull_all(archive, client, repo_info=info, confirm=confirm)

    # The size-less file is in the count (2 files) but not the sum (100 B).
    assert any("2 file" in prompt and "100 B" in prompt for prompt in prompts)
    assert (model_dir(archive) / "hf-snapshot" / "tokenizer.json").is_file()


def test_all_with_no_base_model_offers_repo_id_as_grouping_default(archive, fake_hub_factory):
    # Spec 0004: grouping inverts for original repos — no base_model
    # defaults the canonical directory to the repo id, confirm-gated.
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    client = make_snapshot_client(fake_hub_factory, base_model=None)
    do_pull_all(archive, client, model=None, confirm=confirm)

    assert any(SNAPSHOT_REPO_ID in prompt for prompt in prompts)
    assert (archive / "models" / "acme" / "tiny-orig" / "hf-snapshot" / "config.json").is_file()


def test_declined_repo_id_grouping_under_all_names_model_flag(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return False

    client = make_snapshot_client(fake_hub_factory, base_model=None)
    with pytest.raises(hub.PullUserError) as excinfo:
        do_pull_all(archive, client, model=None, confirm=confirm)

    assert any(SNAPSHOT_REPO_ID in prompt for prompt in prompts)  # default was offered
    assert "--model" in str(excinfo.value)
    assert list((archive / "models").iterdir()) == []


def test_model_override_under_all_skips_the_grouping_prompt(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    client = make_snapshot_client(fake_hub_factory, base_model=None)
    do_pull_all(archive, client, model="custom/name", confirm=confirm)

    assert len(prompts) == 1  # only the size + count confirmation
    assert (archive / "models" / "custom" / "name" / "hf-snapshot" / "config.json").is_file()


def test_refresh_docs_replaces_changed_in_tree_doc_under_all(archive, fake_hub_factory):
    do_pull_all(archive, make_snapshot_client(fake_hub_factory))
    new_readme = b"# tiny original, revised\n"
    changed = make_snapshot_client(
        fake_hub_factory,
        files=[(p, new_readme if p == "README.md" else c, lfs) for p, c, lfs in SNAPSHOT_FILES],
    )

    do_pull_all(archive, changed, refresh_docs=True)

    doc = model_dir(archive) / "hf-snapshot" / "README.md"
    assert doc.read_bytes() == new_readme
    assert doc.stat().st_mode & 0o222 == 0  # re-locked after replacement
    files = {f.path: f for a in load_record(model_dir(archive)).artifacts for f in a.files}
    assert files["hf-snapshot/README.md"].sha256 == hashlib.sha256(new_readme).hexdigest()
    manifest = (model_dir(archive) / "manifest-sha256.txt").read_text(encoding="utf-8")
    assert hashlib.sha256(new_readme).hexdigest() in manifest
    assert hashlib.sha256(README_BYTES).hexdigest() not in manifest


def test_refresh_docs_under_all_never_replaces_a_changed_weight(archive, fake_hub_factory):
    do_pull_all(archive, make_snapshot_client(fake_hub_factory))
    changed = make_snapshot_client(
        fake_hub_factory,
        files=[
            (p, b"shard two, changed" if p == SHARD_2 else c, lfs) for p, c, lfs in SNAPSHOT_FILES
        ],
    )

    with pytest.raises(hub.PullIntegrityError):
        do_pull_all(archive, changed, refresh_docs=True)

    assert (model_dir(archive) / "hf-snapshot" / SHARD_2).read_bytes() == b"shard two bytes"


def test_all_pull_logs_n_of_m_counter_per_file(archive, fake_hub_factory, caplog):
    with caplog.at_level(logging.INFO):
        do_pull_all(archive, make_snapshot_client(fake_hub_factory))

    messages = [record.getMessage() for record in caplog.records]
    assert any("1 of 4" in message and "config.json" in message for message in messages)
    assert any("4 of 4" in message and "README.md" in message for message in messages)


def test_selective_pull_logs_n_of_m_counter_per_file(archive, fake_hub_factory, caplog):
    # Ratified at plan review: the counter logs on ALL pulls, not only
    # --all — one download loop, no mode flag.
    client = make_snapshot_client(fake_hub_factory)
    with caplog.at_level(logging.INFO):
        pull.pull_model(
            archive,
            SNAPSHOT_REPO_ID,
            client,
            include=[SHARD_1],
            model="acme/tiny-chat",
            confirm=lambda prompt: True,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("1 of 2" in message and SHARD_1 in message for message in messages)
    assert any("2 of 2" in message and "README.md" in message for message in messages)


def test_rerun_of_completed_all_pull_downloads_nothing(archive, fake_hub_factory):
    client = make_snapshot_client(fake_hub_factory)
    do_pull_all(archive, client)
    client.download_calls.clear()

    do_pull_all(archive, client)

    assert client.download_calls == []


def test_rerun_of_all_pull_downloads_only_missing_files(archive, fake_hub_factory):
    client = make_snapshot_client(fake_hub_factory)
    do_pull_all(archive, client)
    target = model_dir(archive) / "hf-snapshot" / SHARD_2
    target.chmod(0o644)
    target.unlink()
    client.download_calls.clear()

    do_pull_all(archive, client)

    assert client.download_calls == [SHARD_2]
    assert target.read_bytes() == b"shard two bytes"
