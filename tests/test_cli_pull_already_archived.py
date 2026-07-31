"""CLI behavior of a fully-archived re-pull (spec 0014).

Without ``--model``, a re-pull of a repo that is its own default home
(no ``base_model``) whose whole selection is already archived must
exit 0 with the already-archived report — today it dies at the
grouping confirmation ("confirmation needed but stdin is not
interactive", exit 2) before revealing there was nothing to do.
``--plan`` on the same selection renders the full report with no
``would ask`` grouping line; a *hub-derived* home (declared
``base_model``) keeps its grouping question, and its would-ask line,
even when fully archived. Everything runs via typer.testing.CliRunner
with the hub-client seam faked; no network.
"""

import contextlib

from click import unstyle
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
# Seeds the archive at the repo's own home — exactly the default a
# base_model-less repo proposes, so re-pulls without --model find a
# complete selection there.
SEED_ARGS = ("--include", "*Q4_K_M*", "--model", "bartowski/tiny-chat-GGUF", "--yes")


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
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def seed_default_home(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))
    seeded = runner.invoke(app, ["pull", REPO_ID, str(archive), *SEED_ARGS])
    assert seeded.exit_code == 0
    return archive


def test_fully_archived_repull_without_model_exits_zero_noninteractive(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = seed_default_home(tmp_path, monkeypatch, fake_hub_factory)
    repull_client = fake_hub_factory(base_model=None)
    install_fake_hub(monkeypatch, repull_client)

    # No --model, no --yes, no stdin: today this exits 2 at the
    # grouping confirmation; spec 0014 reports already-archived at 0.
    result = runner.invoke(app, ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*"])

    assert result.exit_code == 0
    output = unstyle(combined_output(result))
    assert "already archived" in output
    assert str(archive / "models" / "bartowski" / "tiny-chat-GGUF") in output
    assert repull_client.download_calls == []
    # The final line says what happened; the pull-success wording is
    # reserved for runs that moved bytes or wrote a record.
    assert "nothing new to pull" in output
    assert f"pulled {REPO_ID} into" not in output


def test_interactive_listing_early_exits_after_selection(tmp_path, monkeypatch, fake_hub_factory):
    # No --include: the file listing still asks for patterns first;
    # when the chosen files are all archived, nothing else is asked.
    # Only the pattern line is consumed — a grouping prompt would hit
    # end-of-input and exit 2, failing this test.
    archive = seed_default_home(tmp_path, monkeypatch, fake_hub_factory)
    listing_client = fake_hub_factory(base_model=None)
    install_fake_hub(monkeypatch, listing_client)

    result = runner.invoke(app, ["pull", REPO_ID, str(archive)], input="*Q4_K_M*\n")

    assert result.exit_code == 0
    output = unstyle(combined_output(result))
    assert "already archived" in output
    assert listing_client.download_calls == []


def test_plan_on_fully_archived_selection_has_no_would_ask_grouping(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = seed_default_home(tmp_path, monkeypatch, fake_hub_factory)
    plan_client = fake_hub_factory(base_model=None)
    install_fake_hub(monkeypatch, plan_client)

    result = runner.invoke(app, ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", "--plan"])

    assert result.exit_code == 0
    output = unstyle(combined_output(result))
    assert "already archived" in output  # the report still renders in full
    assert "would ask:" not in output  # no question survives a no-op plan
    assert plan_client.download_calls == []


def test_plan_with_hub_derived_home_keeps_would_ask_grouping(
    tmp_path, monkeypatch, fake_hub_factory
):
    # A hub-derived home (declared base_model) always confirms — so its
    # --plan keeps the would-ask line even on a fully-archived
    # selection: the line mirrors what the real pull would ask.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())  # base_model=acme/tiny-chat
    seeded = runner.invoke(
        app,
        [
            "pull",
            REPO_ID,
            str(archive),
            "--include",
            "*Q4_K_M*",
            "--model",
            "acme/tiny-chat",
            "--yes",
        ],
    )
    assert seeded.exit_code == 0
    plan_client = fake_hub_factory()
    install_fake_hub(monkeypatch, plan_client)

    result = runner.invoke(app, ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", "--plan"])

    assert result.exit_code == 0
    output = unstyle(combined_output(result))
    assert "would ask:" in output
    assert f"group {REPO_ID} under canonical model acme/tiny-chat?" in output
    assert plan_client.download_calls == []
