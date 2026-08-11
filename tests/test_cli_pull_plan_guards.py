"""Guard tests for ``pull --plan``: parity, archive gate, sanitization.

Spec 0005: plan exit 0 must mean the identical real command would
proceed (so --plan validates --role and requires an initialized
archive), and hub-supplied text in the report must never carry raw
terminal control characters. CliRunner + faked hub seam; no network.
"""

import contextlib

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"


def combined_output(result) -> str:
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
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke_plan(archive, *extra_args):
    return runner.invoke(
        app,
        [
            "pull",
            REPO_ID,
            str(archive),
            "--include",
            "*Q4_K_M*",
            "--plan",
            *extra_args,
        ],
    )


def test_plan_on_uninitialized_path_fails_like_a_real_pull(tmp_path, monkeypatch):
    # No hub client is ever constructed: the archive gate fires first.
    not_an_archive = tmp_path / "not-an-archive"
    not_an_archive.mkdir()

    result = runner.invoke(
        app,
        ["pull", REPO_ID, str(not_an_archive), "--include", "*Q4_K_M*", "--plan"],
    )

    assert result.exit_code == 1
    assert "archive" in combined_output(result).lower()


def test_plan_with_bad_role_exits_2_like_a_real_pull(tmp_path, monkeypatch, fake_hub_factory):
    # Plan exit 0 is a promise the real command would proceed; a bad
    # --role must fail identically under --plan (exit 2).
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_plan(archive, "--role", "bogus")

    assert result.exit_code == 2
    assert "unknown role" in combined_output(result)
    assert client.download_calls == []


def test_plan_report_strips_terminal_control_characters(tmp_path, monkeypatch, fake_hub_factory):
    # base_model is hub-controlled and lands in the master advisory; an
    # embedded ESC must never reach the terminal raw.
    archive = init_archive_dir(tmp_path)
    hostile = "evil/\x1b[2J\x1b]0;owned\x07model"
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=hostile))

    result = invoke_plan(archive)

    output = combined_output(result)
    assert result.exit_code == 0
    assert "advisory:" in output
    assert "\x1b" not in output
