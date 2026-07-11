"""Tests for llm_preserver.pull — record-merge semantics on re-pull.

Spec 0003 review adjudications (2026-07-10): per-file revision pins,
never-erase hub facts (pipeline_tag), and v1 artifacts (no
source_repo) as merge targets. Everything drives the FakeHubClient
from conftest; no network.
"""

import hashlib

import pytest

import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
Q8_NAME = "tiny-chat-Q8_0.gguf"
README_BYTES = b"# tiny-chat quantized\n"
HUB_FILES = [
    (Q4_NAME, Q4_BYTES, True),
    (Q8_NAME, b"q8 weight bytes", True),
    ("README.md", README_BYTES, False),
]
COMMIT_A = "a" * 40  # FakeHubClient's default resolved commit
COMMIT_B = "b" * 40
DOCS_REL = "gguf/docs/bartowski--tiny-chat-GGUF"


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


def model_dir(archive_root):
    return archive_root / "models" / "acme" / "tiny-chat"


def test_mixed_commit_merge_keeps_per_file_revisions(archive, fake_hub_factory):
    # Files pulled at commit A keep their own pin when a later pull at
    # commit B merges into the same artifact; only artifact.revision
    # moves to the most recent pull's commit.
    do_pull(archive, make_client(fake_hub_factory))
    later = make_client(fake_hub_factory, commit=COMMIT_B)

    do_pull(archive, later, include=["*Q8*"])

    record = load_record(model_dir(archive))
    [artifact] = record.artifacts
    assert artifact.revision == COMMIT_B
    files = {f.path: f for f in artifact.files}
    assert files[f"gguf/{Q4_NAME}"].revision == COMMIT_A
    assert files[f"gguf/{Q8_NAME}"].revision == COMMIT_B
    assert files[f"{DOCS_REL}/README.md"].revision == COMMIT_A


def test_repull_with_null_pipeline_tag_preserves_recorded_value(archive, fake_hub_factory):
    # Hub facts are never erased: a repo that stops reporting a
    # pipeline_tag must not blank what an earlier pull recorded.
    do_pull(archive, make_client(fake_hub_factory, pipeline_tag="text-generation"))
    later = make_client(fake_hub_factory, pipeline_tag=None)

    do_pull(archive, later, include=["*Q8*"])

    assert load_record(model_dir(archive)).pipeline_tag == "text-generation"


def test_v1_artifact_without_source_repo_is_merge_target(
    archive, fake_hub_factory, write_model, sample_record_dict
):
    # A v1-era record carries no source_repo; re-pulling the same format
    # fills the source in rather than appending a duplicate artifact.
    q4_sha = hashlib.sha256(Q4_BYTES).hexdigest()
    existing = sample_record_dict(
        artifacts=[
            {
                "format": "gguf",
                "source_repo": None,
                "revision": COMMIT_A,
                "provenance": "verified",
                "files": [
                    {
                        "path": f"gguf/{Q4_NAME}",
                        "sha256": q4_sha,
                        "size": len(Q4_BYTES),
                        "source": "original",
                    }
                ],
            }
        ],
    )
    write_model(archive, existing)
    target = model_dir(archive) / "gguf" / Q4_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Q4_BYTES)

    do_pull(archive, make_client(fake_hub_factory))

    record = load_record(model_dir(archive))
    [artifact] = record.artifacts  # merged, not duplicated
    assert artifact.source_repo == f"https://huggingface.co/{REPO_ID}"
    manifest = (model_dir(archive) / "manifest-sha256.txt").read_text(encoding="utf-8")
    manifest_paths = [line.split(maxsplit=1)[1] for line in manifest.splitlines()]
    assert len(manifest_paths) == len(set(manifest_paths))  # one line per path
