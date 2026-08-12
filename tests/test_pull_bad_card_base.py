"""An unusable ``base_model`` on a model card must not crash a pull.

Review finding, 2026-08-11: `update_record` fed the card's value
straight into `ModelRecord`, whose lineage validator raises — so a card
carrying a URL, a bare model name, or free text produced a raw pydantic
traceback *after* the payload had already landed, leaving bytes in the
archive with no record and no manifest. Re-running crashed again, so
the repo became permanently unarchivable.

Real cards do all of these. The tool already prints an advisory saying
the value is not a usable repo id, then recorded it anyway. Dropping an
unusable claim keeps the advisory as the signal and the archive
writable — the tool never invents lineage, and refusing to record a
claim it cannot represent is the same discipline.
"""

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.records import load_record

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
UNUSABLE = [
    "https://huggingface.co/acme/tiny-chat",
    "Llama-3-8B",
    "acme/tiny-chat/tree/main",
    "acme/tiny chat (v2)",
    "../../evil?x=",
]


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    assert runner.invoke(app, ["init", str(archive)]).exit_code == 0
    return archive


@pytest.mark.parametrize("claim", UNUSABLE)
def test_an_unusable_card_claim_does_not_crash_the_pull(
    tmp_path, monkeypatch, fake_hub_factory, claim
):
    import llm_preserver.cli as cli_module

    archive = init_archive_dir(tmp_path)
    monkeypatch.setattr(cli_module, "HubClient", lambda: fake_hub_factory(base_model=claim))

    result = runner.invoke(app, ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", "--yes"])

    assert result.exit_code == 0, click.unstyle(result.output)


def test_the_record_is_written_with_no_lineage_rather_than_none_at_all(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The failure this replaces left payload in the archive with no
    # record at all — the worst outcome, since nothing described it.
    import llm_preserver.cli as cli_module

    archive = init_archive_dir(tmp_path)
    monkeypatch.setattr(cli_module, "HubClient", lambda: fake_hub_factory(base_model="Llama-3-8B"))

    runner.invoke(app, ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", "--yes"])

    record = load_record(archive / "models" / "bartowski" / "tiny-chat-GGUF")
    assert record.base_model is None
    assert record.base_model_source is None
    assert record.artifacts  # the payload is described


def test_a_usable_card_claim_is_still_recorded(tmp_path, monkeypatch, fake_hub_factory):
    import llm_preserver.cli as cli_module

    archive = init_archive_dir(tmp_path)
    monkeypatch.setattr(cli_module, "HubClient", lambda: fake_hub_factory())

    runner.invoke(app, ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", "--yes"])

    record = load_record(archive / "models" / "bartowski" / "tiny-chat-GGUF")
    assert (record.base_model, record.base_model_source) == ("acme/tiny-chat", "card")
