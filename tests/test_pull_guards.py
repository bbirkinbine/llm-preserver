"""Guard tests for llm_preserver.pull — hostile input and fault paths.

Split from test_pull.py (300-line rule): these pin the failure-path
matrix — path traversal, local filesystem faults, corrupt staging,
case collisions, selection guards, confirmation wiring, and the
pull_plan hard stops. Everything drives the FakeHubClient from
conftest; no network.
"""

import pytest

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.records import load_record

FULL_COMMIT_HASH = "a" * 40  # FakeHubClient's default resolved commit
REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
README_BYTES = b"# tiny-chat quantized\n"
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


def test_absolute_hub_filename_is_rejected_before_download(tmp_path, archive, fake_hub_factory):
    # An absolute rfilename must fail path validation on its own; a
    # naive join would reset to the filesystem root and escape both
    # staging and the archive.
    evil = tmp_path / "evil.gguf"
    client = make_client(
        fake_hub_factory,
        files=[(str(evil), b"evil bytes", True), ("README.md", README_BYTES, False)],
    )

    with pytest.raises(hub.PullUserError):
        do_pull(archive, client, include=["*evil*"])

    assert client.download_calls == []
    assert not evil.exists()
    assert list((archive / "models").iterdir()) == []


def test_local_filesystem_failure_maps_to_env_error(archive, fake_hub_factory):
    # A read-only models/ tree is a local-environment fault (PermissionError),
    # not a raw traceback with exit code 1.
    (archive / "models").chmod(0o555)
    try:
        with pytest.raises(hub.PullEnvError):
            do_pull(archive, make_client(fake_hub_factory))
    finally:
        (archive / "models").chmod(0o755)


def test_corrupt_download_is_discarded_from_staging(archive, fake_hub_factory):
    # A hash-mismatched staged file (and its client bookkeeping) must
    # be dropped, or the client would reuse the corrupt bytes on every
    # retry and "retry the pull" could never succeed.
    client = make_client(fake_hub_factory)
    original_download = client.download

    def corrupting_download(repo_id, filename, revision, dest_dir):
        path = original_download(repo_id, filename, revision, dest_dir)
        path.write_bytes(b"corrupted bytes")
        return path

    client.download = corrupting_download

    with pytest.raises(hub.PullIntegrityError):
        do_pull(archive, client)

    staging = archive / ".staging" / "acme" / "tiny-chat"
    assert not (staging / Q4_NAME).exists()
    assert not (staging / ".cache" / "huggingface" / "download" / f"{Q4_NAME}.metadata").exists()
    assert list((archive / "models").iterdir()) == []


def test_case_colliding_selection_is_rejected(archive, fake_hub_factory):
    # README.md and readme.md are one file on APFS/NTFS; moving both
    # would silently corrupt the archive on case-insensitive systems.
    files = [
        ("README.md", b"upper", False),
        ("readme.md", b"lower", False),
        (Q4_NAME, Q4_BYTES, True),
    ]
    client = make_client(fake_hub_factory, files=files)

    with pytest.raises(hub.PullUserError) as excinfo:
        do_pull(archive, client)

    message = str(excinfo.value)
    assert "README.md" in message
    assert "readme.md" in message
    assert client.download_calls == []


def test_zero_matching_include_patterns_hard_stop(archive, fake_hub_factory):
    # Docs always ride, so a zero-match pattern would otherwise archive
    # only a README and stamp a wrong-format artifact.
    client = make_client(fake_hub_factory)

    with pytest.raises(hub.PullUserError) as excinfo:
        do_pull(archive, client, include=["*NOPE*"])

    assert "--include" in str(excinfo.value)
    assert Q4_NAME in str(excinfo.value)  # names available files
    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_empty_include_list_hard_stops(archive, fake_hub_factory):
    client = make_client(fake_hub_factory)

    with pytest.raises(hub.PullUserError):
        do_pull(archive, client, include=[])

    assert client.download_calls == []


def test_every_weight_selection_requires_confirmation(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull(archive, make_client(fake_hub_factory), include=["*.gguf"], confirm=confirm)

    assert any("every weight" in prompt for prompt in prompts)
    assert (model_dir(archive) / "gguf" / Q4_NAME).is_file()


def test_declined_every_weight_pull_writes_nothing(archive, fake_hub_factory):
    client = make_client(fake_hub_factory)

    with pytest.raises(hub.PullUserError):
        do_pull(archive, client, include=["*.gguf"], confirm=lambda prompt: False)

    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_unrecorded_on_disk_file_matching_hub_hash_is_adopted(archive, fake_hub_factory):
    # Reconcile-by-hash (spec 0003 adjudications): a crash between move
    # and record-write leaves the file on disk but unrecorded. If its
    # hash matches the hub, it is adopted — recorded, not re-downloaded.
    target = model_dir(archive) / "gguf" / Q4_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(Q4_BYTES)
    client = make_client(fake_hub_factory)

    do_pull(archive, client)

    assert Q4_NAME not in client.download_calls
    files = {f.path: f for a in load_record(model_dir(archive)).artifacts for f in a.files}
    entry = files[f"gguf/{Q4_NAME}"]
    assert entry.provenance == "verified"
    assert entry.revision == FULL_COMMIT_HASH


def test_unrecorded_on_disk_file_with_different_content_still_refuses(archive, fake_hub_factory):
    # Reconcile-by-hash only adopts a proven match; any mismatch (or a
    # hub file with no published hash) keeps the refuse-forever stop —
    # overwriting would destroy bytes the record never described.
    target = model_dir(archive) / "gguf" / Q4_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"stray unrecorded bytes")

    with pytest.raises(hub.PullIntegrityError):
        do_pull(archive, make_client(fake_hub_factory))

    assert target.read_bytes() == b"stray unrecorded bytes"


def test_no_base_model_offers_repo_id_as_default_grouping(archive, fake_hub_factory):
    # Spec 0004 (ratified 2026-07-11): the repo-id grouping default
    # applies to pull GENERALLY — this amends 0003's no-metadata hard
    # stop (replaces test_pull.py::test_no_base_model_and_no_override_
    # hard_stops). No base_model → confirm the repo id itself.
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    client = make_client(fake_hub_factory, base_model=None)
    do_pull(archive, client, model=None, confirm=confirm)

    assert any(REPO_ID in prompt for prompt in prompts)
    target = archive / "models" / "bartowski" / "tiny-chat-GGUF" / "gguf" / Q4_NAME
    assert target.is_file()


def test_declined_repo_id_default_grouping_names_model_flag(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return False

    client = make_client(fake_hub_factory, base_model=None)
    with pytest.raises(hub.PullUserError) as excinfo:
        do_pull(archive, client, model=None, confirm=confirm)

    assert any(REPO_ID in prompt for prompt in prompts)  # the default was offered
    assert "--model" in str(excinfo.value)
    assert list((archive / "models").iterdir()) == []


def test_present_but_malformed_base_model_still_hard_stops(archive, fake_hub_factory):
    # Regression pin: the 0004 repo-id default applies only when
    # base_model is ABSENT; present-but-unusable metadata stays a stop.
    client = make_client(fake_hub_factory, base_model="not a valid id")

    with pytest.raises(hub.PullUserError):
        do_pull(archive, client, model=None, confirm=lambda prompt: True)

    assert list((archive / "models").iterdir()) == []


def test_hashless_size_change_is_hard_stop(archive, fake_hub_factory):
    # No hub hash to compare: a name match with a differing size is
    # ambiguous, and the archive is payload-immutable — refuse to guess.
    client = make_client(fake_hub_factory, files=[("config.json", b'{"a": 1}', False)])
    do_pull(archive, client, include=["*.json"])

    changed = make_client(fake_hub_factory, files=[("config.json", b'{"a": 12}', False)])
    with pytest.raises(hub.PullIntegrityError):
        do_pull(archive, changed, include=["*.json"])

    assert (model_dir(archive) / "hf-snapshot" / "config.json").read_bytes() == b'{"a": 1}'
