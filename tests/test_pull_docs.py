"""Tests for llm_preserver.pull — doc placement and --refresh-docs.

Spec 0003 review adjudications (2026-07-10): docs land under
``<format>/docs/<namespace>--<repo>/`` so source repos never collide,
and ``--refresh-docs`` is the explicit choice for replacing changed
upstream docs. Weights never honor the flag. Everything drives the
FakeHubClient from conftest; no network.
"""

import hashlib

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

REPO_ID = "bartowski/tiny-chat-GGUF"
OTHER_REPO_ID = "unsloth/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
README_BYTES = b"# tiny-chat quantized\n"
HUB_FILES = [
    (Q4_NAME, Q4_BYTES, True),
    ("README.md", README_BYTES, False),
]
DOCS_REL = "gguf/docs/bartowski--tiny-chat-GGUF"
OTHER_DOCS_REL = "gguf/docs/unsloth--tiny-chat-GGUF"


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def make_client(fake_hub_factory, **overrides):
    overrides.setdefault("files", HUB_FILES)
    return fake_hub_factory(**overrides)


def do_pull(archive_root, client, repo_id=REPO_ID, **kwargs):
    kwargs.setdefault("include", ["*Q4_K_M*"])
    kwargs.setdefault("model", "acme/tiny-chat")
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(archive_root, repo_id, client, **kwargs)


def model_dir(archive_root):
    return archive_root / "models" / "acme" / "tiny-chat"


def test_two_source_repos_never_collide_on_docs(archive, fake_hub_factory):
    # Same format, same doc filename, different source repos: each gets
    # its own docs directory and both are recorded — no hard stop.
    do_pull(archive, make_client(fake_hub_factory))
    other_readme = b"# same model, different quant shop\n"
    other = make_client(
        fake_hub_factory,
        files=[
            ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
            ("README.md", other_readme, False),
        ],
    )

    do_pull(archive, other, repo_id=OTHER_REPO_ID, include=["*Q8*"])

    assert (model_dir(archive) / DOCS_REL / "README.md").read_bytes() == README_BYTES
    assert (model_dir(archive) / OTHER_DOCS_REL / "README.md").read_bytes() == other_readme
    record = load_record(model_dir(archive))
    recorded_paths = {f.path for a in record.artifacts for f in a.files}
    assert f"{DOCS_REL}/README.md" in recorded_paths
    assert f"{OTHER_DOCS_REL}/README.md" in recorded_paths


def test_changed_doc_without_flag_hard_stops_naming_refresh_docs(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    changed = make_client(
        fake_hub_factory,
        files=[(Q4_NAME, Q4_BYTES, True), ("README.md", b"# tiny-chat quantized, v2\n", False)],
    )

    with pytest.raises(hub.PullIntegrityError) as excinfo:
        do_pull(archive, changed)

    assert "--refresh-docs" in str(excinfo.value)
    # The archived doc is untouched.
    assert (model_dir(archive) / DOCS_REL / "README.md").read_bytes() == README_BYTES


def test_refresh_docs_replaces_changed_doc_and_relocks(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    new_readme = b"# tiny-chat quantized, v2\n"
    changed = make_client(
        fake_hub_factory, files=[(Q4_NAME, Q4_BYTES, True), ("README.md", new_readme, False)]
    )

    do_pull(archive, changed, refresh_docs=True)

    doc = model_dir(archive) / DOCS_REL / "README.md"
    assert doc.read_bytes() == new_readme
    assert doc.stat().st_mode & 0o222 == 0  # re-locked after replacement
    files = {f.path: f for a in load_record(model_dir(archive)).artifacts for f in a.files}
    entry = files[f"{DOCS_REL}/README.md"]
    assert entry.sha256 == hashlib.sha256(new_readme).hexdigest()
    assert entry.size == len(new_readme)
    manifest = (model_dir(archive) / "manifest-sha256.txt").read_text(encoding="utf-8")
    assert hashlib.sha256(new_readme).hexdigest() in manifest
    assert hashlib.sha256(README_BYTES).hexdigest() not in manifest


def test_refresh_docs_never_replaces_changed_weights(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    changed = make_client(
        fake_hub_factory,
        files=[(Q4_NAME, b"q4 weight bytes v2", True), ("README.md", README_BYTES, False)],
    )

    with pytest.raises(hub.PullIntegrityError) as excinfo:
        do_pull(archive, changed, refresh_docs=True)

    assert "--refresh-docs" not in str(excinfo.value)
    assert (model_dir(archive) / "gguf" / Q4_NAME).read_bytes() == Q4_BYTES
