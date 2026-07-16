"""Tests for llm_preserver.cli pull — failures, fault domains, and output hygiene.

Split from test_cli_pull.py (300-line rule). Covers the error half of
the pull command: the four fault-domain exit codes, the integrity and
invalid-repo-id user messages, and the control-character scrubbing of
hub- and user-supplied text before it is echoed. Everything runs inside
tmp_path via typer.testing.CliRunner with the hub-client seam faked (or,
for the invalid-id test, the real client rejecting locally); no network
is ever touched.
"""

import contextlib
import logging

import click
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


def test_pull_invalid_repo_id_exits_2_without_traceback(tmp_path, monkeypatch):
    # Spec 0011: an Ollama-style `name:tag` pasted where a hub `org/name`
    # id is expected must print one clean line and exit 2 (user-input
    # fault domain), never a rich Traceback.
    #
    # This exercises the REAL hub client on purpose: the fix lives in the
    # client's `except MAPPED_EXCEPTIONS -> map_hub_exception` seam, so a
    # FakeHubClient (which raises canned Pull*Error instances) would bypass
    # exactly the code under test. The library's `validate_repo_id` rejects
    # the id locally, before any HTTP request, so no network is touched;
    # HF_HUB_OFFLINE is set as a belt regardless.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(
        app,
        ["pull", "qwen3-vl:30b-a3b-instruct", str(archive), "--include", "*", "--yes"],
    )

    output = click.unstyle(combined_output(result))
    assert result.exit_code == 2, output
    # The command handled the bad input; it did not let the library's
    # HFValidationError escape as an unhandled crash (which CliRunner
    # surfaces as a non-SystemExit result.exception and exit_code 1).
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(
        result.exception
    )
    assert "Traceback (most recent call last)" not in output
    assert "qwen3-vl:30b-a3b-instruct" in output
