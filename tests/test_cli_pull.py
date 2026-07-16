"""Tests for llm_preserver.cli — the pull command.

Split from test_cli.py (300-line rule). Everything runs inside
tmp_path via typer.testing.CliRunner with the hub-client seam faked;
no network is ever touched.
"""

import contextlib
import json

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    """Swap the CLI's hub-client seam for a fake.

    Pins the seam: the pull command constructs its client via
    ``llm_preserver.cli.HubClient`` (imported from ``llm_preserver.hub``),
    so tests can replace it without any network.
    """
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke_pull(archive, *extra_args):
    # --yes auto-accepts the size confirmation that rides every pull
    # (spec 0005); these tests pin other behavior and stay non-interactive.
    args = [
        "pull",
        "bartowski/tiny-chat-GGUF",
        str(archive),
        "--include",
        "*Q4_K_M*",
        "--model",
        "acme/tiny-chat",
        "--yes",
        *extra_args,
    ]
    return runner.invoke(app, args)


def test_pull_downloads_selected_files_into_archive(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive)

    assert result.exit_code == 0
    assert (archive / "models/acme/tiny-chat/gguf/tiny-chat-Q4_K_M.gguf").is_file()
    assert (archive / "models/acme/tiny-chat/model-record.json").is_file()


def test_pull_include_flag_is_repeatable(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    files = [
        ("tiny-chat-Q4_K_M.gguf", b"q4 weight bytes", True),
        ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
        ("config.json", b"{}", False),
    ]
    install_fake_hub(monkeypatch, fake_hub_factory(files=files))

    result = invoke_pull(archive, "--include", "*.json")

    assert result.exit_code == 0
    gguf_dir = archive / "models/acme/tiny-chat/gguf"
    assert (gguf_dir / "tiny-chat-Q4_K_M.gguf").is_file()
    assert (gguf_dir / "config.json").is_file()
    assert not (gguf_dir / "tiny-chat-Q8_0.gguf").exists()


def test_pull_role_flag_sets_roles(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--role", "chat")

    assert result.exit_code == 0
    record = json.loads((archive / "models/acme/tiny-chat/model-record.json").read_text())
    assert record["roles"] == ["chat"]


def test_pull_interactive_selection_lists_files_and_pulls(tmp_path, monkeypatch, fake_hub_factory):
    # No --include: the CLI lists the repo's files with sizes (one
    # metadata call), prompts for patterns, and pulls the selection.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        ["pull", "bartowski/tiny-chat-GGUF", str(archive), "--model", "acme/tiny-chat"],
        input="*Q4_K_M*\ny\n",  # patterns, then the size confirmation (spec 0005)
    )

    assert result.exit_code == 0
    output = combined_output(result)
    assert "tiny-chat-Q4_K_M.gguf" in output
    assert "tiny-chat-Q8_0.gguf" in output
    assert str(len(b"q4 weight bytes")) in output  # sizes are listed
    assert (archive / "models/acme/tiny-chat/gguf/tiny-chat-Q4_K_M.gguf").is_file()


def test_pull_interactive_blank_input_is_user_error(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        ["pull", "bartowski/tiny-chat-GGUF", str(archive), "--model", "acme/tiny-chat"],
        input="\n",
    )

    assert result.exit_code == 2  # user-input fault domain
    assert list((archive / "models").iterdir()) == []


def test_pull_validates_archive_before_prompting(tmp_path, monkeypatch, fake_hub_factory):
    # A bad archive path must fail fast — before any metadata call or
    # interactive prompt.
    not_archive = tmp_path / "not-an-archive"
    not_archive.mkdir()

    class ExplodingHubClient:
        def repo_info(self, repo_id):
            raise AssertionError("repo_info must not be called for an invalid archive")

        def download(self, repo_id, filename, revision, dest_dir):
            raise AssertionError("download must not be called for an invalid archive")

    install_fake_hub(monkeypatch, ExplodingHubClient())

    result = runner.invoke(app, ["pull", "bartowski/tiny-chat-GGUF", str(not_archive)])

    assert result.exit_code != 0
    assert "archive" in combined_output(result).lower()


def test_interactive_listing_annotates_recognized_companion_kinds(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The advisory rules table doubles as a file-kind legend where the
    # human reads filenames (live-use ask 2026-07-13: "what is imatrix
    # again?"). Plain weights get no note.
    archive = init_archive_dir(tmp_path)
    files = [
        ("tiny-chat-Q4_K_M.gguf", b"q4 weight bytes", True),
        ("tiny-chat.imatrix", b"imatrix bytes", False),
        ("mmproj-F16.gguf", b"projector bytes", True),
    ]
    install_fake_hub(monkeypatch, fake_hub_factory(files=files))

    result = runner.invoke(
        app,
        ["pull", "bartowski/tiny-chat-GGUF", str(archive), "--model", "acme/tiny-chat"],
        input="\n",
    )

    output = combined_output(result)
    listing = [line for line in output.splitlines() if "  " in line]
    imatrix_line = next(line for line in listing if "tiny-chat.imatrix" in line)
    assert "quantization calibration data" in imatrix_line
    mmproj_line = next(line for line in listing if "mmproj-F16.gguf" in line)
    assert "vision projector" in mmproj_line
    q4_line = next(line for line in listing if "Q4_K_M" in line)
    assert "—" not in q4_line.split("gguf", 1)[1] if "gguf" in q4_line else True
