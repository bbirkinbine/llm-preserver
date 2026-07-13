"""Same-repo ordering tests: selective pull and --whole-repo interleaved.

docs/cli.md promises the selective/snapshot doc-placement difference is
"additive duplication, never a conflict" when both shapes pull the same
repo into the same model. Pins both orders: the relocated doc copy and
the in-tree copy coexist, the record loads, and the manifest holds
exactly one verifiable line per path. FakeHubClient from conftest; no
network.
"""

import hashlib

import pytest

import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
README_BYTES = b"# tiny-chat quantized\n"
GGUF_FILES = [
    (Q4_NAME, b"q4 weight bytes", True),
    ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
    ("README.md", README_BYTES, False),
]
RELOCATED_DOC = "gguf/docs/bartowski--tiny-chat-GGUF/README.md"
IN_TREE_DOC = "gguf/README.md"


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def make_client(fake_hub_factory):
    return fake_hub_factory(files=GGUF_FILES, repo_id=REPO_ID)


def do_pull(archive_root, client, **kwargs):
    kwargs.setdefault("include", ["*Q4_K_M*"])
    kwargs.setdefault("model", "acme/tiny-chat")
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(archive_root, REPO_ID, client, **kwargs)


def do_pull_all(archive_root, client, **kwargs):
    kwargs.setdefault("include", ())
    kwargs.setdefault("model", "acme/tiny-chat")
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(archive_root, REPO_ID, client, select_all=True, **kwargs)


def model_dir(archive_root):
    return archive_root / "models" / "acme" / "tiny-chat"


def assert_both_copies_and_manifest_verifies(mdir):
    """Both doc copies on disk and recorded; manifest unique and valid."""
    assert (mdir / RELOCATED_DOC).read_bytes() == README_BYTES
    assert (mdir / IN_TREE_DOC).read_bytes() == README_BYTES
    record = load_record(mdir)  # the record still loads
    recorded_paths = {f.path for a in record.artifacts for f in a.files}
    assert {RELOCATED_DOC, IN_TREE_DOC} <= recorded_paths
    manifest_lines = (mdir / "manifest-sha256.txt").read_text(encoding="utf-8").splitlines()
    paths = [line.split(maxsplit=1)[1] for line in manifest_lines]
    assert len(paths) == len(set(paths))  # exactly one line per path
    for line in manifest_lines:  # and every line shasum-verifies
        sha, path = line.split(maxsplit=1)
        assert hashlib.sha256((mdir / path).read_bytes()).hexdigest() == sha, path


def test_selective_then_all_is_additive_never_a_conflict(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))

    do_pull_all(archive, make_client(fake_hub_factory))

    assert_both_copies_and_manifest_verifies(model_dir(archive))


def test_all_then_selective_is_additive_never_a_conflict(archive, fake_hub_factory):
    do_pull_all(archive, make_client(fake_hub_factory))

    do_pull(archive, make_client(fake_hub_factory))

    assert_both_copies_and_manifest_verifies(model_dir(archive))
