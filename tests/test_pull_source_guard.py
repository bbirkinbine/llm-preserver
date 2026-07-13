"""One source repo per format subdirectory (spec 0004 adjudications).

A tree-verbatim ``--whole-repo`` into a format subdirectory whose recorded
artifact came from a *different* source repo is refused with the ways
out (--model, or a selective pull). Same-repo mixing stays additive;
selective pulls keep their 0003 cross-repo behavior. FakeHubClient
from conftest; no network.
"""

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

REPO_A = "bartowski/tiny-chat-GGUF"
REPO_B = "unsloth/tiny-chat-GGUF"
FILES_A = [
    ("tiny-chat-Q4_K_M.gguf", b"q4 from bartowski", True),
    ("README.md", b"# bartowski quants\n", False),
]
FILES_B = [
    ("tiny-chat-Q8_0.gguf", b"q8 from unsloth", True),
    ("README.md", b"# unsloth quants\n", False),
]


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def do_pull(archive_root, client, repo_id, *, select_all, **kwargs):
    kwargs.setdefault("include", () if select_all else ["*.gguf"])
    kwargs.setdefault("model", "acme/tiny-chat")
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(archive_root, repo_id, client, select_all=select_all, **kwargs)


def model_dir(archive_root):
    return archive_root / "models" / "acme" / "tiny-chat"


def test_second_all_from_different_repo_is_refused_naming_both(archive, fake_hub_factory):
    do_pull(archive, fake_hub_factory(files=FILES_A, repo_id=REPO_A), REPO_A, select_all=True)

    with pytest.raises(hub.PullUserError) as excinfo:
        do_pull(archive, fake_hub_factory(files=FILES_B, repo_id=REPO_B), REPO_B, select_all=True)

    message = str(excinfo.value)
    assert REPO_A in message  # the existing owner
    assert REPO_B in message  # the refused newcomer
    assert "--model" in message
    # The refused pull changed nothing.
    assert not (model_dir(archive) / "gguf" / "tiny-chat-Q8_0.gguf").exists()


def test_same_repo_all_rerun_is_still_fine(archive, fake_hub_factory):
    do_pull(archive, fake_hub_factory(files=FILES_A, repo_id=REPO_A), REPO_A, select_all=True)
    client = fake_hub_factory(files=FILES_A, repo_id=REPO_A)

    do_pull(archive, client, REPO_A, select_all=True)  # no refusal

    assert client.download_calls == []  # and nothing re-downloads


def test_all_into_subdir_populated_by_selective_from_other_repo_is_refused(
    archive, fake_hub_factory
):
    # Decided by artifact source_repo match, not file collision: the
    # selective pull's docs were relocated, so nothing would collide —
    # the subdir is still owned by the other repo.
    selective = fake_hub_factory(files=FILES_A, repo_id=REPO_A)
    do_pull(archive, selective, REPO_A, select_all=False, include=["*Q4_K_M*"])

    with pytest.raises(hub.PullUserError) as excinfo:
        do_pull(archive, fake_hub_factory(files=FILES_B, repo_id=REPO_B), REPO_B, select_all=True)

    assert REPO_A in str(excinfo.value)
    assert REPO_B in str(excinfo.value)


def test_selective_after_all_from_other_repo_still_lands_weights(archive, fake_hub_factory):
    # Selective pulls keep the 0003 cross-repo behavior: docs relocate,
    # weight filenames are distinct, no ownership refusal.
    do_pull(archive, fake_hub_factory(files=FILES_A, repo_id=REPO_A), REPO_A, select_all=True)

    do_pull(
        archive,
        fake_hub_factory(files=FILES_B, repo_id=REPO_B),
        REPO_B,
        select_all=False,
        include=["*Q8_0*"],
    )

    assert (model_dir(archive) / "gguf" / "tiny-chat-Q8_0.gguf").read_bytes() == b"q8 from unsloth"
    record = load_record(model_dir(archive))
    sources = {a.source_repo for a in record.artifacts}
    assert sources == {f"https://huggingface.co/{REPO_A}", f"https://huggingface.co/{REPO_B}"}
