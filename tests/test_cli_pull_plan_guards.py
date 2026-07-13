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
            "--model",
            "acme/tiny-chat",
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


def test_plan_advises_when_model_override_contradicts_declared_base(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The live footgun repro (2026-07-12): --model copy-pasted from a
    # different model's pull. The conftest repo declares
    # base_model=acme/tiny-chat; --model names an unrelated directory.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        [
            "pull",
            REPO_ID,
            str(archive),
            "--include",
            "*Q4_K_M*",
            "--model",
            "Qwen/Qwen3.6-35B-A3B",
            "--plan",
        ],
    )

    output = combined_output(result)
    assert result.exit_code == 0
    warning_lines = [
        line
        for line in output.splitlines()
        if line.startswith("warning:")
        and "acme/tiny-chat" in line
        and "Qwen/Qwen3.6-35B-A3B" in line
    ]
    assert warning_lines, output
    # Human error outranks missing companions: the warning precedes
    # every advisory: line in the report.
    first_warning = output.index(warning_lines[0])
    advisory_positions = [
        output.index(line) for line in output.splitlines() if line.startswith("advisory:")
    ]
    assert all(first_warning < pos for pos in advisory_positions)


def test_real_pull_prints_the_mismatch_advisory_but_still_honors_model(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Advisory only: the pull proceeds into the directory --model named.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(
        app,
        [
            "pull",
            REPO_ID,
            str(archive),
            "--include",
            "*Q4_K_M*",
            "--model",
            "Qwen/Qwen3.6-35B-A3B",
            "--yes",
        ],
    )

    output = combined_output(result)
    assert result.exit_code == 0
    assert "acme/tiny-chat" in output  # the declared base, named in the advisory
    assert (archive / "models" / "Qwen" / "Qwen3.6-35B-A3B" / "model-record.json").is_file()


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
