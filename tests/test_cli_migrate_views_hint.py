"""``migrate``'s view-tree hint — spec 0017 pass 2, criteria 18-19.

Migration invalidates every runtime view: an Ollama store's blob
symlinks point at old archive paths, and views are disposable by design
(ADR 0001), so the repair is a regeneration the human runs. The archive
cannot enumerate the trees that exist — the marker points dest → archive
and never the other way (spec 0017 non-goals) — so ``migrate`` prints
the command for each dest it was *told* about with ``--view-dest``, and
says unconditionally that any tree it was not told about is stale.

The path is composed into text and **never opened**: ``migrate`` does not
touch a view tree, it only says what to run. Every test here proves that
by pointing ``--view-dest`` at a path that does not exist.

Expected red (test-first): the `migrate` command is not registered.
"""

import re
from pathlib import Path

import pytest
from migrate_shapes import (
    init_archive_dir,
    output_of,
    pure_rename_archive,
    split_archive,
)
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """An initialized archive holding one pure rename and one split."""
    root = init_archive_dir(tmp_path)
    pure_rename_archive(root)
    split_archive(root)
    return root


def invoke_migrate(archive: Path, *extra: str):
    """Run ``migrate <archive> [extra...]`` through the CliRunner."""
    return runner.invoke(app, ["migrate", str(archive), *extra])


def test_view_dest_is_composed_into_a_runnable_views_command(archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "views" / "ollama"

    result = invoke_migrate(archive, "--yes", "--view-dest", str(dest))

    assert result.exit_code == 0
    out = output_of(result)
    hint = next(line for line in out.splitlines() if "views" in line and str(dest) in line)
    assert str(archive.resolve()) in hint  # runnable from any directory


def test_the_view_dest_path_is_never_opened(archive: Path, tmp_path: Path) -> None:
    """Criterion 19: ``migrate`` does not touch a view tree. A dest that
    does not exist must stay that way — no probe, no mkdir, no marker
    read, and no error either."""
    dest = tmp_path / "views" / "ollama"

    result = invoke_migrate(archive, "--yes", "--view-dest", str(dest))

    assert result.exit_code == 0
    assert not dest.exists()
    assert not dest.parent.exists()


def test_view_dest_is_repeatable_and_each_gets_its_own_command(
    archive: Path, tmp_path: Path
) -> None:
    """A human can be running several stores; each needs its own line,
    since the command differs only by the dest."""
    first = tmp_path / "views-a" / "ollama"
    second = tmp_path / "views-b" / "ollama"

    result = invoke_migrate(archive, "--yes", "--view-dest", str(first), "--view-dest", str(second))

    assert result.exit_code == 0
    out = output_of(result)
    assert any("views" in line and str(first) in line for line in out.splitlines())
    assert any("views" in line and str(second) in line for line in out.splitlines())


def test_the_stale_view_warning_prints_without_any_view_dest(archive: Path) -> None:
    """Unconditional (criterion 19): no archive-side registry exists, so
    the tool cannot know whether a view tree is out there — it says so
    instead of staying quiet and letting the human find out at
    model-load time."""
    result = invoke_migrate(archive, "--yes")

    assert result.exit_code == 0
    assert re.search(r"stale", output_of(result), re.IGNORECASE)


def test_the_stale_view_warning_prints_even_when_dests_were_named(
    archive: Path, tmp_path: Path
) -> None:
    """Naming one dest is no evidence that it is the only one."""
    dest = tmp_path / "views" / "ollama"

    result = invoke_migrate(archive, "--yes", "--view-dest", str(dest))

    assert result.exit_code == 0
    assert re.search(r"stale", output_of(result), re.IGNORECASE)


def test_a_plan_run_names_the_view_commands_without_migrating(
    archive: Path, tmp_path: Path
) -> None:
    """The rehearsal must disclose the follow-up work too — regenerating
    a large store is part of what the human is deciding to spend."""
    dest = tmp_path / "views" / "ollama"

    result = invoke_migrate(archive, "--plan", "--view-dest", str(dest))

    assert result.exit_code == 0
    out = output_of(result)
    assert str(dest) in out
    assert not dest.exists()
    assert (archive / "models" / "Qwen" / "tiny-chat").is_dir()  # still unmigrated
