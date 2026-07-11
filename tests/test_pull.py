"""Tests for llm_preserver.pull — selective-pull orchestration.

Everything drives the ``FakeHubClient`` from conftest; no network.
Pins the seam from the spec-0003 plan:

    pull_model(archive_root, repo_id, client, *, include, model=None,
               roles=(), confirm=<callable taking a prompt string>)

Files stage under ``<root>/.staging/``, are hashed, then move to
``models/<creator>/<model>/<format>/``; the record is written last.
"""

import contextlib
import hashlib
import logging

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

FULL_COMMIT_HASH = "a" * 40  # FakeHubClient's default resolved commit
REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
Q4_SHA = hashlib.sha256(Q4_BYTES).hexdigest()
README_BYTES = b"# tiny-chat quantized\n"
README_SHA = hashlib.sha256(README_BYTES).hexdigest()
HUB_FILES = [
    (Q4_NAME, Q4_BYTES, True),
    ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
    ("README.md", README_BYTES, False),
]


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


# Docs land in a per-source-repo directory (spec 0003 adjudications).
DOCS_REL = "gguf/docs/bartowski--tiny-chat-GGUF"


def test_pull_places_files_under_format_subdir(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    gguf_dir = model_dir(archive) / "gguf"
    assert (gguf_dir / Q4_NAME).read_bytes() == Q4_BYTES
    assert (model_dir(archive) / DOCS_REL / "README.md").read_bytes() == README_BYTES


def test_pull_does_not_fetch_unselected_quants(archive, fake_hub_factory):
    client = make_client(fake_hub_factory)
    do_pull(archive, client)
    assert "tiny-chat-Q8_0.gguf" not in client.download_calls
    assert not (model_dir(archive) / "gguf" / "tiny-chat-Q8_0.gguf").exists()


def test_record_pins_resolved_commit(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    record = load_record(model_dir(archive))
    assert record.artifacts[0].revision == FULL_COMMIT_HASH


def test_hub_hashed_weight_is_verified_per_file(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    files = {f.path: f for f in load_record(model_dir(archive)).artifacts[0].files}
    assert files[f"gguf/{Q4_NAME}"].provenance == "verified"
    assert files[f"gguf/{Q4_NAME}"].sha256 == Q4_SHA


def test_hashless_readme_is_hashed_locally_per_file(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    files = {f.path: f for f in load_record(model_dir(archive)).artifacts[0].files}
    assert files[f"{DOCS_REL}/README.md"].provenance == "hashed-locally"
    assert files[f"{DOCS_REL}/README.md"].sha256 == README_SHA


def test_artifact_provenance_demoted_by_hashless_file(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    assert load_record(model_dir(archive)).artifacts[0].provenance == "hashed-locally"


def test_artifact_with_all_files_verified_is_verified(archive, fake_hub_factory):
    client = make_client(fake_hub_factory, files=[(Q4_NAME, Q4_BYTES, True)])
    do_pull(archive, client)
    assert load_record(model_dir(archive)).artifacts[0].provenance == "verified"


def test_pipeline_tag_recorded_verbatim(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory, pipeline_tag="text-generation"))
    assert load_record(model_dir(archive)).pipeline_tag == "text-generation"


def test_roles_left_empty_without_role_assignment(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    assert load_record(model_dir(archive)).roles == []


def test_manifest_covers_payload_and_record(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    manifest = (model_dir(archive) / "manifest-sha256.txt").read_text(encoding="utf-8")
    assert Q4_SHA in manifest
    assert f"gguf/{Q4_NAME}" in manifest
    # The record's manifest line must hash the record file exactly as
    # written — a byte-coupling pin on the manifest-then-record order.
    record_line = next(line for line in manifest.splitlines() if line.endswith("model-record.json"))
    record_bytes = (model_dir(archive) / "model-record.json").read_bytes()
    assert record_line.split()[0] == hashlib.sha256(record_bytes).hexdigest()


def test_payload_files_are_write_protected(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    gguf_dir = model_dir(archive) / "gguf"
    assert (gguf_dir / Q4_NAME).stat().st_mode & 0o222 == 0
    assert (model_dir(archive) / DOCS_REL / "README.md").stat().st_mode & 0o222 == 0


def test_no_client_bookkeeping_left_in_archive(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    assert [p for p in archive.rglob("*") if ".cache" in p.parts] == []
    staging = archive / ".staging"
    if staging.exists():
        assert [p for p in staging.rglob("*") if p.is_file()] == []


def test_repull_with_matching_hashes_downloads_nothing(archive, fake_hub_factory, caplog):
    client = make_client(fake_hub_factory)
    do_pull(archive, client)
    client.download_calls.clear()
    with caplog.at_level(logging.INFO):
        do_pull(archive, client)
    assert client.download_calls == []
    assert "already archived" in caplog.text


def test_hashless_file_skips_on_name_and_size_match(archive, fake_hub_factory):
    # The hub publishes no sha256 for non-LFS files; the skip falls
    # back to a name + size match against the record.
    client = make_client(fake_hub_factory, files=[("config.json", b'{"a": 1}', False)])
    do_pull(archive, client, include=["*.json"])
    client.download_calls.clear()
    do_pull(archive, client, include=["*.json"])
    assert client.download_calls == []


def test_changed_upstream_hash_hard_stops_showing_both_hashes(archive, fake_hub_factory):
    do_pull(archive, make_client(fake_hub_factory))
    new_bytes = b"q4 weight bytes v2"
    updated = make_client(
        fake_hub_factory,
        files=[(Q4_NAME, new_bytes, True), ("README.md", README_BYTES, False)],
    )
    with pytest.raises(hub.PullIntegrityError) as excinfo:
        do_pull(archive, updated)
    message = str(excinfo.value)
    assert Q4_SHA in message
    assert hashlib.sha256(new_bytes).hexdigest() in message
    # Never a silent overwrite: archived bytes are untouched.
    assert (model_dir(archive) / "gguf" / Q4_NAME).read_bytes() == Q4_BYTES


def test_recorded_file_missing_on_disk_warns_and_redownloads(archive, fake_hub_factory, caplog):
    client = make_client(fake_hub_factory)
    do_pull(archive, client)
    target = model_dir(archive) / "gguf" / Q4_NAME
    target.chmod(0o644)
    target.unlink()
    client.download_calls.clear()
    with caplog.at_level(logging.WARNING):
        do_pull(archive, client)
    assert Q4_NAME in client.download_calls
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(Q4_NAME in message for message in warnings)
    assert target.read_bytes() == Q4_BYTES


def test_interrupted_pull_records_nothing(archive, fake_hub_factory):
    # Record is written only after ALL selected files are fully on
    # disk and hashed (spec invariant); a mid-pull failure after the
    # first file must leave no record behind.
    client = make_client(
        fake_hub_factory,
        download_error=hub.PullHubError("hub returned 500; retry later"),
        fail_after_downloads=1,
    )
    with pytest.raises(hub.PullHubError):
        do_pull(archive, client)
    assert list((archive / "models").rglob("model-record.json")) == []


def test_model_override_used_verbatim_without_confirmation(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull(archive, make_client(fake_hub_factory), model="custom/name", confirm=confirm)
    assert (archive / "models" / "custom" / "name" / "gguf" / Q4_NAME).is_file()
    assert prompts == []


def test_base_model_grouping_is_confirmed_with_user(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull(archive, make_client(fake_hub_factory), model=None, confirm=confirm)
    assert any("acme/tiny-chat" in prompt for prompt in prompts)
    assert (model_dir(archive) / "gguf" / Q4_NAME).is_file()


def test_declined_grouping_writes_nothing(archive, fake_hub_factory):
    with contextlib.suppress(hub.PullUserError):
        do_pull(archive, make_client(fake_hub_factory), model=None, confirm=lambda prompt: False)
    assert list((archive / "models").iterdir()) == []


def test_repull_updates_record_without_clobbering_artifacts(
    archive, fake_hub_factory, write_model, sample_record_dict
):
    existing = sample_record_dict(
        roles=["chat"],
        artifacts=[
            {
                "format": "mlx",
                "provenance": "unverified",
                "files": [
                    {
                        "path": "mlx/model.safetensors",
                        "sha256": "1" * 64,
                        "size": 10,
                        "source": "original",
                    }
                ],
            }
        ],
    )
    write_model(archive, existing)
    do_pull(archive, make_client(fake_hub_factory))
    record = load_record(model_dir(archive))
    assert {artifact.format for artifact in record.artifacts} == {"mlx", "gguf"}
    assert record.roles == ["chat"]
