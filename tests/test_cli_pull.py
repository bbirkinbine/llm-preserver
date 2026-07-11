"""Tests for llm_preserver.cli — the pull command.

Split from test_cli.py (300-line rule). Everything runs inside
tmp_path via typer.testing.CliRunner with the hub-client seam faked;
no network is ever touched.
"""

import contextlib
import json
import logging

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


class FailingHubClient:
    """Hub-seam double whose repo_info raises a fault-domain error."""

    def __init__(self, exc):
        self._exc = exc

    def repo_info(self, repo_id):
        raise self._exc

    def download(self, repo_id, filename, revision, dest_dir):
        raise AssertionError("download must not be called after repo_info failed")


def invoke_pull(archive, *extra_args):
    args = [
        "pull",
        "bartowski/tiny-chat-GGUF",
        str(archive),
        "--include",
        "*Q4_K_M*",
        "--model",
        "acme/tiny-chat",
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
        input="*Q4_K_M*\n",
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


def test_pull_fault_domains_have_distinct_exit_codes(tmp_path, monkeypatch):
    from llm_preserver.hub import (
        PullEnvError,
        PullHubError,
        PullIntegrityError,
        PullUserError,
    )

    archive = init_archive_dir(tmp_path)
    failures = {
        "user": PullUserError("unknown repo id acme/nope: check the repo id"),
        "env": PullEnvError("network unreachable: check your connection"),
        "hub": PullHubError("hub returned 503: retry later"),
        "integrity": PullIntegrityError("sha256 mismatch after download: retry the pull"),
    }
    codes = {}
    for domain, exc in failures.items():
        install_fake_hub(monkeypatch, FailingHubClient(exc))
        result = invoke_pull(archive)
        assert result.exit_code != 0, domain
        codes[domain] = result.exit_code
    assert len(set(codes.values())) == 4


def test_pull_integrity_failure_message_names_domain_and_next_step(tmp_path, monkeypatch):
    from llm_preserver.hub import PullIntegrityError

    archive = init_archive_dir(tmp_path)
    exc = PullIntegrityError("sha256 mismatch for tiny-chat-Q4_K_M.gguf: retry the pull")
    install_fake_hub(monkeypatch, FailingHubClient(exc))

    result = invoke_pull(archive)

    assert result.exit_code != 0
    output = combined_output(result)
    assert "integrity" in output.lower()
    assert "retry the pull" in output


def test_pull_verbose_failure_never_leaks_authorization_header(tmp_path, monkeypatch, caplog):
    import httpx
    from huggingface_hub.errors import HfHubHTTPError

    from llm_preserver.hub import PullHubError

    leaked_value = "hf_FAKETOKEN12345"
    request = httpx.Request(
        "GET",
        "https://huggingface.co/api/models/bartowski/tiny-chat-GGUF",
        headers={"Authorization": f"Bearer {leaked_value}"},
    )
    raw = HfHubHTTPError("500 Server Error", response=httpx.Response(500, request=request))
    wrapped = PullHubError("hub-side failure: retry later")
    wrapped.__cause__ = raw
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, FailingHubClient(wrapped))

    with caplog.at_level(logging.DEBUG):
        result = invoke_pull(archive, "--verbose")

    assert result.exit_code != 0
    everything = combined_output(result) + caplog.text
    assert leaked_value not in everything
    assert "Authorization" not in everything


def test_grouping_prompt_sanitizes_hostile_base_model(tmp_path, monkeypatch, fake_hub_factory):
    # base_model is hub-supplied text and reaches the confirm prompt;
    # a value carrying terminal escapes must render control-char-free.
    archive = init_archive_dir(tmp_path)
    hostile = "acme/tiny\x1b]52;c;evil\x07chat"
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=hostile))

    result = runner.invoke(
        app,
        ["pull", "bartowski/tiny-chat-GGUF", str(archive), "--include", "*Q4_K_M*"],
        input="n\n",  # decline the grouping; the prompt has already rendered
    )

    assert result.exit_code != 0
    output = combined_output(result)
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "evil" in output  # content survives, escapes do not
