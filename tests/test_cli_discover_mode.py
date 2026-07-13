"""The discover archive-mode prompt (spec 0006, live-use 2026-07-13).

Picking "pull this repo" asks HOW to archive: 1 = pick files (the
selective flow), 2 = whole-repo snapshot (spec 0004 semantics —
originals/masters are trees, not menus). Split from
test_cli_discover_flow.py at the 300-line cap.
"""

import contextlib

from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.hub_discovery import ModelSummary

runner = CliRunner()

QUANT_REPO = "bartowski/tiny-chat-GGUF"
BASE_MODEL = "acme/tiny-chat"


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


def summary(repo_id, **overrides):
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def type_lines(*lines) -> str:
    return "".join(f"{line}\n" for line in lines)


def quant_client(fake_hub_factory):
    return fake_hub_factory(
        search_results=[summary(QUANT_REPO, downloads=41, base_model=BASE_MODEL)],
        summaries={BASE_MODEL: summary(BASE_MODEL, downloads=999)},
    )


def invoke_discover(archive, stdin):
    return runner.invoke(app, ["discover", "tiny", str(archive)], input=stdin)


def test_snapshot_mode_pulls_the_whole_tree_without_a_file_listing(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Archive mode 2 = the spec-0004 whole-repo snapshot: no pattern
    # prompt, one grouping confirm, one size confirm, tree verbatim.
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("1", "0", "2", "y", "y"))

    assert result.exit_code == 0
    output = combined_output(result)
    assert "files to pull" not in output  # no pattern prompt in snapshot mode
    assert "3 of 3 files" in output  # the whole tree in one confirmation
    record = archive / "models" / "acme" / "tiny-chat" / "model-record.json"
    assert record.is_file()
    assert len(client.download_calls) == 3


def test_quit_at_the_archive_mode_prompt_pulls_nothing(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("1", "0", "q"))

    assert result.exit_code == 0
    assert client.download_calls == []
