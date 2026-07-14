"""Non-interactive prompt behavior for pull (spec 0004 adjudications).

When stdin cannot answer a confirmation, the CLI converts it to a
deterministic ``PullUserError`` exit (2) naming the bypass — grouping
names ``--model``, the size confirmation names ``--yes``. ``--yes``
auto-accepts the size confirmation only, never grouping. CliRunner's
stdin raises EOF when no ``input=`` is given, which is exactly the
unanswerable-prompt case. No network.
"""

import contextlib

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

GGUF_REPO_ID = "bartowski/tiny-chat-GGUF"


def combined_output(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def init_archive_dir(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    result = runner.invoke(app, ["init", str(archive_dir)])
    assert result.exit_code == 0
    return archive_dir


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def test_unanswerable_grouping_prompt_exits_2_naming_model_flag(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())  # base_model present, gguf tree

    result = runner.invoke(
        app,
        ["pull", GGUF_REPO_ID, str(archive), "--include", "*Q4_K_M*"],
        # no input=: the grouping confirm cannot be answered
    )

    assert result.exit_code == 2
    output = combined_output(result)
    assert "--model" in output
    assert not (archive / "models" / "acme").exists()


def test_unanswerable_whole_repo_size_confirm_exits_2_naming_yes(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        ["pull", GGUF_REPO_ID, str(archive), "--whole-repo", "--model", "acme/tiny-chat"],
        # --model skips grouping; the size confirm cannot be answered
    )

    assert result.exit_code == 2
    assert "--yes" in combined_output(result)


def test_whole_repo_with_yes_and_model_runs_without_any_prompt(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        ["pull", GGUF_REPO_ID, str(archive), "--whole-repo", "--model", "acme/tiny-chat", "--yes"],
        # no input needed at all
    )

    assert result.exit_code == 0
    assert (archive / "models/acme/tiny-chat/gguf/tiny-chat-Q4_K_M.gguf").is_file()


def test_yes_never_accepts_the_grouping_confirm(tmp_path, monkeypatch, fake_hub_factory):
    # --yes covers the size confirmation only; identity still needs
    # --model (or an interactive answer).
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        ["pull", GGUF_REPO_ID, str(archive), "--whole-repo", "--yes"],
        # grouping confirm still fires and cannot be answered
    )

    assert result.exit_code == 2
    assert "--model" in combined_output(result)


def test_interactive_listing_prints_human_sizes_not_raw_bytes(monkeypatch, capsys):
    """The listing is the fit-vs-VRAM decision aid (live-use 2026-07-12):
    19851335840 tells a human nothing; 18.5 GiB is the signal."""
    import typer

    from llm_preserver.cli.pull_exec.prompts import prompt_for_selection
    from llm_preserver.hub import RepoFile, RepoInfo

    info = RepoInfo(
        commit="0" * 40,
        files=[
            RepoFile(path="tiny-chat-Q4_K_M.gguf", size=19851335840, sha256="a" * 64),
            RepoFile(path="README.md", size=512, sha256=None),
            RepoFile(path="mystery.bin", size=None, sha256=None),
        ],
        base_model=None,
        pipeline_tag=None,
        license=None,
    )
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "")

    prompt_for_selection(info, "bartowski/tiny-chat-GGUF")

    out = capsys.readouterr().out
    assert "18.5 GiB" in out
    assert "19851335840" not in out  # raw bytes are gone
    assert "512 B" in out
    assert "?" in out  # unknown size still renders as ?
