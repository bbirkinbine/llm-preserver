"""Tests for llm_preserver.pull — the --all full-snapshot shape (spec 0004).

``pull --all`` archives a repo's whole tree at the pinned commit into
the model's format subdirectory, preserving repo-relative paths (tree
fidelity: in-tree docs stay put, unlike 0003's selective relocation).
The per-file selection prompts are replaced by one file-count +
total-size confirmation. Pins the seam from the spec-0004 plan:

    pull_model(..., select_all=False)  # True → selection = whole tree

Everything drives the FakeHubClient from conftest; no network.
"""

import contextlib

import pytest
from typer.testing import CliRunner

import llm_preserver.hub as hub
import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.cli import app
from llm_preserver.records import load_record

FULL_COMMIT_HASH = "a" * 40  # FakeHubClient's default resolved commit
SNAPSHOT_REPO_ID = "acme/tiny-orig"
SHARD_1 = "model-00001-of-00002.safetensors"
SHARD_2 = "model-00002-of-00002.safetensors"
NESTED_SHARD = "BF16/model-00001-of-00002.safetensors"
SNAPSHOT_FILES = [
    # A sharded original: config + tokenizer + shards, one nested path,
    # in-tree README. Only runnable as a tree (spec 0004).
    ("config.json", b'{"architectures": ["TinyChat"]}', False),
    (SHARD_1, b"shard one bytes", True),
    (SHARD_2, b"shard two bytes", True),
    (NESTED_SHARD, b"nested bf16 shard bytes", True),
    ("tokenizer.json", b'{"version": 1}', False),
    ("README.md", b"# tiny original\n", False),
]
GGUF_REPO_ID = "bartowski/tiny-chat-GGUF"
GGUF_FILES = [
    ("tiny-chat-Q4_K_M.gguf", b"q4 weight bytes", True),
    ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
    ("README.md", b"# tiny-chat quantized\n", False),
]

runner = CliRunner()


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


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def init_archive_dir(tmp_path):
    archive_dir = tmp_path / "cli-archive"
    archive_dir.mkdir()
    result = runner.invoke(app, ["init", str(archive_dir)])
    assert result.exit_code == 0
    return archive_dir


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


class ExplodingHubClient:
    """Hub double that must never be reached (flag conflicts fail first)."""

    def repo_info(self, repo_id):
        raise AssertionError("repo_info must not be called")

    def download(self, repo_id, filename, revision, dest_dir):
        raise AssertionError("download must not be called")


def test_cli_all_with_include_exits_2_naming_both_flags(tmp_path, monkeypatch):
    # --all and --include are mutually exclusive (spec 0004); the
    # conflict is a user-input fault raised before any network call.
    archive_dir = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, ExplodingHubClient())

    result = runner.invoke(
        app,
        ["pull", GGUF_REPO_ID, str(archive_dir), "--all", "--include", "*.gguf"],
    )

    assert result.exit_code == 2
    output = combined_output(result)
    assert "--all" in output
    assert "--include" in output


def test_cli_all_pulls_whole_tree_without_interactive_selection(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive_dir = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(files=GGUF_FILES))

    result = runner.invoke(
        app,
        ["pull", GGUF_REPO_ID, str(archive_dir), "--all", "--model", "acme/tiny-chat"],
        input="y\n",  # the single size + count confirmation
    )

    assert result.exit_code == 0
    assert "files to pull" not in combined_output(result)  # no pattern prompt
    gguf_dir = archive_dir / "models" / "acme" / "tiny-chat" / "gguf"
    assert (gguf_dir / "tiny-chat-Q4_K_M.gguf").is_file()
    assert (gguf_dir / "tiny-chat-Q8_0.gguf").is_file()
    assert (gguf_dir / "README.md").is_file()


def test_all_archives_every_file_preserving_repo_relative_paths(archive, fake_hub_factory):
    do_pull_all(archive, make_snapshot_client(fake_hub_factory))

    snapshot = model_dir(archive) / "hf-snapshot"
    for path, content, _is_lfs in SNAPSHOT_FILES:
        assert (snapshot / path).read_bytes() == content
    # The nested shard kept its directory, beside the flat shards.
    assert (snapshot / NESTED_SHARD).parent.name == "BF16"


def test_all_keeps_docs_at_their_in_tree_paths(archive, fake_hub_factory):
    # Tree fidelity (spec 0004): a snapshot's README stays at its hub
    # path, never relocated to the 0003 docs/<source-repo>/ directory.
    do_pull_all(archive, make_snapshot_client(fake_hub_factory))

    snapshot = model_dir(archive) / "hf-snapshot"
    assert (snapshot / "README.md").read_bytes() == b"# tiny original\n"
    assert not (snapshot / "docs").exists()


def test_all_records_snapshot_format_with_null_quantization(archive, fake_hub_factory):
    do_pull_all(archive, make_snapshot_client(fake_hub_factory))

    artifact = load_record(model_dir(archive)).artifacts[0]
    assert artifact.format == "hf-snapshot"
    assert artifact.quantization is None
    assert artifact.revision == FULL_COMMIT_HASH


def test_all_records_per_file_provenance_and_revision(archive, fake_hub_factory):
    do_pull_all(archive, make_snapshot_client(fake_hub_factory))

    files = {f.path: f for a in load_record(model_dir(archive)).artifacts for f in a.files}
    shard = files[f"hf-snapshot/{SHARD_1}"]
    assert shard.provenance == "verified"  # hub-declared LFS hash matched
    assert shard.revision == FULL_COMMIT_HASH
    readme = files["hf-snapshot/README.md"]
    assert readme.provenance == "hashed-locally"  # hub publishes no hash
    assert readme.revision == FULL_COMMIT_HASH


def test_gguf_repo_tree_lands_in_gguf_subdir_under_all(archive, fake_hub_factory):
    do_pull_all(archive, fake_hub_factory(files=GGUF_FILES), repo_id=GGUF_REPO_ID)

    gguf_dir = model_dir(archive) / "gguf"
    assert (gguf_dir / "tiny-chat-Q4_K_M.gguf").is_file()
    assert (gguf_dir / "tiny-chat-Q8_0.gguf").is_file()
    assert load_record(model_dir(archive)).artifacts[0].format == "gguf"


def test_mlx_community_repo_lands_in_mlx_subdir_under_all(archive, fake_hub_factory):
    mlx_files = [("model.safetensors", b"mlx weights", True), ("config.json", b"{}", False)]
    client = fake_hub_factory(files=mlx_files, repo_id="mlx-community/tiny-chat-4bit")

    do_pull_all(archive, client, repo_id="mlx-community/tiny-chat-4bit")

    assert (model_dir(archive) / "mlx" / "model.safetensors").is_file()
    assert load_record(model_dir(archive)).artifacts[0].format == "mlx"


def test_all_confirms_once_with_file_count_and_total_size_only(archive, fake_hub_factory):
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    do_pull_all(archive, make_snapshot_client(fake_hub_factory), confirm=confirm)

    assert len(prompts) == 1  # --model given: the size confirm is the only prompt
    prompt = prompts[0]
    total = sum(len(content) for _path, content, _is_lfs in SNAPSHOT_FILES)
    assert f"{len(SNAPSHOT_FILES)} file" in prompt
    assert f"{total} B" in prompt  # human-readable total (bytes-scale here)
    # Listing 500 files is noise (spec 0004): no filenames in the prompt.
    assert "config.json" not in prompt
    assert "README.md" not in prompt
    assert ".safetensors" not in prompt


def test_declined_all_confirmation_downloads_and_writes_nothing(archive, fake_hub_factory):
    client = make_snapshot_client(fake_hub_factory)

    with pytest.raises(hub.PullUserError):
        do_pull_all(archive, client, confirm=lambda prompt: False)

    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_all_never_triggers_the_every_weight_confirmation(archive, fake_hub_factory):
    # --all inevitably selects every weight; the 0003 every-weight
    # prompt is replaced by the single size + count confirm — never both.
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    client = fake_hub_factory(files=GGUF_FILES, repo_id=GGUF_REPO_ID)
    do_pull_all(archive, client, repo_id=GGUF_REPO_ID, confirm=confirm)

    assert len(prompts) == 1
    assert not any("every weight" in prompt for prompt in prompts)
