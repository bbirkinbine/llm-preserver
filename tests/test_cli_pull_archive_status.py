"""The real pull and discover entry points expose current archive evidence."""

from pathlib import Path
from typing import Any  # Existing fake Hub and pytest fixture factories are dynamic.

import click
import pytest
from migrate_shapes import pure_rename_archive
from test_cli_discover_flow import (
    QUANT_REPO,
    init_archive_dir,
    install_fake_hub,
    invoke_discover,
    quant_client,
    runner,
    type_lines,
)

from llm_preserver.cli import app


def seed_archive(archive: Path, client: Any) -> None:
    """Archive one real tiny fake-Hub quant through the public command."""
    result = runner.invoke(
        app, ["pull", QUANT_REPO, str(archive), "--include", "*Q4_K_M*", "--yes"]
    )
    assert result.exit_code == 0, result.output
    client.download_calls.clear()
    client.repo_info_calls.clear()


def listing_prefix(output: str) -> str:
    """Only the picker frame, before planning or transfer can annotate output."""
    plain = click.unstyle(output)
    assert "files to pull" in plain
    return plain.split("files to pull", 1)[0].split(f"files in {QUANT_REPO}", 1)[1]


@pytest.mark.parametrize("entrypoint", ["pull", "discover"])
def test_picker_shows_archived_and_unarchived_files_before_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub_factory: Any, entrypoint: str
) -> None:
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)
    seed_archive(archive, client)
    if entrypoint == "pull":
        result = runner.invoke(app, ["pull", QUANT_REPO, str(archive), "--plan"], input="*Q8_0*\n")
    else:
        result = invoke_discover(archive, "--plan", stdin=type_lines("1", "0", "1", "*Q8_0*"))
    assert result.exit_code == 0, result.output
    prefix = listing_prefix(result.output)
    q4 = next(line for line in prefix.splitlines() if "tiny-chat-Q4_K_M.gguf" in line)
    q8 = next(line for line in prefix.splitlines() if "tiny-chat-Q8_0.gguf" in line)
    assert "[archived]" in q4
    assert "[not archived]" in q8
    assert str(archive) in prefix
    assert "metadata" in prefix.lower()
    assert "checksum" in prefix.lower()
    assert "size only" in prefix
    assert client.repo_info_calls == [QUANT_REPO]
    assert client.download_calls == []


def test_partial_selection_keeps_existing_transfer_and_skip_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub_factory: Any
) -> None:
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)
    seed_archive(archive, client)
    result = runner.invoke(app, ["pull", QUANT_REPO, str(archive), "--yes"], input="*.gguf\ny\n")
    assert result.exit_code == 0, result.output
    assert "[archived]" in listing_prefix(result.output)
    assert client.download_calls == ["tiny-chat-Q8_0.gguf"]
    assert client.repo_info_calls == [QUANT_REPO]


def test_piped_listing_remains_single_pass_and_accepts_literal_q_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub_factory: Any
) -> None:
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(files=[("q", b"abc", True)])
    install_fake_hub(monkeypatch, client)
    result = runner.invoke(app, ["pull", QUANT_REPO, str(archive), "--plan"], input="q\n")
    assert result.exit_code == 0, result.output
    plain = click.unstyle(result.output)
    assert "[not archived]" in listing_prefix(plain)
    assert plain.count("files to pull") == 1
    assert "q = quit" not in plain
    assert "plan only" in plain


def test_corrupt_record_warns_but_still_reaches_selection_and_existing_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub_factory: Any
) -> None:
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)
    seed_archive(archive, client)
    record = archive / "models" / QUANT_REPO / "model-record.json"
    record.write_text("{broken")
    result = runner.invoke(app, ["pull", QUANT_REPO, str(archive), "--plan"], input="*Q8_0*\n")
    prefix = listing_prefix(result.output)
    assert "status unavailable" in prefix.lower()
    assert "[not archived]" not in prefix
    assert result.exit_code != 0  # The existing planner still refuses the corrupt record.
    assert client.download_calls == []


def test_discover_withholds_status_from_an_unconverted_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub_factory: Any
) -> None:
    """A pre-ADR-0003 directory can hide this repo's files from the lookup.

    Discover reaches the picker before pull's conversion gate refuses, so
    a per-repo answer here would be a confident, wrong ``not archived``.
    """
    archive = init_archive_dir(tmp_path)
    pure_rename_archive(archive)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)
    result = invoke_discover(archive, "--plan", stdin=type_lines("1", "0", "1", "*Q8_0*"))
    prefix = listing_prefix(result.output)
    assert "[not archived]" not in prefix
    assert "archived 0/" not in prefix
    assert "[status unavailable]" in prefix
    assert "migrate" in prefix
    # The pull gate still refuses after the picker, cleanly, not a traceback.
    assert result.exit_code == 2, result.output
    assert "error [user input]" in click.unstyle(result.output)
