"""CLI for `llm-preserver migrate` — spec 0017 pass 2, criteria 9/16/17.

Preview-then-confirm over irreplaceable bytes, the ``remove`` (spec
0010) contract carried over verbatim: ``--yes`` skips the question and
never the disclosure, a non-interactive run without it refuses rather
than acting on a piped answer, and an interrupt reprints the exact
command that resumes the job (spec 0007). ``--plan`` is the rehearsal
that changes nothing.

The ``--view-dest`` hint lives in test_cli_migrate_views_hint.py, the
``--to`` copy mode in test_cli_migrate_to.py, and the v1 content gate in
test_cli_migrate_gate.py.

Expected red (test-first): the `migrate` command is not registered, so
every invoke exits 2 with click's no-such-command usage error — which is
why every exit-2 assertion below also asserts on message content.
"""

import os
import re
from pathlib import Path

import pytest
import typer.testing
from migrate_shapes import (
    Q4,
    Q4_REL,
    Q8,
    Q8_REL,
    RENAME_DIR_ID,
    RENAME_TARGET_ID,
    init_archive_dir,
    output_of,
    pure_rename_archive,
    split_archive,
    stdout_of,
    tree_snapshot,
)
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.pull_preflight import human_size

runner = CliRunner()

TARGET_REL_PATH = "models/unsloth/tiny-chat-GGUF"
"""Printed target path, as a substring of either an absolute or a
model-root-relative rendering."""


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """An initialized archive holding one pure rename and one split."""
    root = init_archive_dir(tmp_path)
    pure_rename_archive(root)
    split_archive(root)
    return root


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an attended terminal so the confirmation prompt runs.

    typer bundles its own click, so the runner's streams are
    ``typer.testing._NamedTextIOWrapper`` (spec 0010's hard-won fact).
    """
    monkeypatch.setattr(typer.testing._NamedTextIOWrapper, "isatty", lambda self: True)


def invoke_migrate(archive: Path, *extra: str, stdin: str | None = None):
    """Run ``migrate <archive> [extra...]`` through the CliRunner."""
    return runner.invoke(app, ["migrate", str(archive), *extra], input=stdin)


def test_plan_changes_nothing_on_disk(archive: Path) -> None:
    """Criterion 9: ``--plan`` is what a human runs against 683 GiB
    before deciding, so it may not move, write, or remove anything."""
    before = tree_snapshot(archive)

    result = invoke_migrate(archive, "--plan")

    assert result.exit_code == 0
    assert tree_snapshot(archive) == before


def test_plan_names_each_directory_its_target_and_the_bytes(archive: Path) -> None:
    result = invoke_migrate(archive, "--plan")

    out = output_of(result)
    assert RENAME_DIR_ID in out  # the directory to be renamed
    assert TARGET_REL_PATH in out  # where its files land
    assert human_size(len(Q4) + len(Q8) + len(Q4)) in out  # the byte total that moves


def test_plan_distinguishes_a_pure_rename_from_a_split(archive: Path) -> None:
    """The two shapes cost different things — one directory disappears,
    the other keeps its own weights — so the preview has to tell them
    apart before the human confirms."""
    result = invoke_migrate(archive, "--plan")

    out = output_of(result)
    assert re.search(r"rename", out, re.IGNORECASE)
    assert re.search(r"split", out, re.IGNORECASE)


def test_plan_names_every_directory_the_run_will_remove(archive: Path) -> None:
    """Criterion 17: each ``os.rmdir`` is named in the preview *before*
    the confirm — this is the only deletion ``migrate`` performs."""
    result = invoke_migrate(archive, "--plan")

    out = output_of(result)
    assert "models/Qwen" in out
    assert re.search(r"remove|rmdir|delete", out, re.IGNORECASE)


def test_confirming_the_preview_migrates_the_archive(archive: Path, interactive: None) -> None:
    result = invoke_migrate(archive, stdin="y\n")

    assert result.exit_code == 0
    assert (archive / TARGET_REL_PATH / Q4_REL).read_bytes() == Q4
    assert not (archive / "models" / "Qwen").exists()


def test_declining_the_confirmation_moves_nothing_and_exits_zero(
    archive: Path, interactive: None
) -> None:
    """ "Nothing migrated" is a successful outcome, not a fault (the
    spec 0010 precedent: an explicit branch, never ``abort=True``)."""
    before = tree_snapshot(archive)

    result = invoke_migrate(archive, stdin="n\n")

    assert result.exit_code == 0
    assert tree_snapshot(archive) == before


def test_yes_prints_the_full_preview_then_the_result_line(archive: Path) -> None:
    """``--yes`` skips the question, never the disclosure: a script's log
    is the only audit trail of a bulk move."""
    result = invoke_migrate(archive, "--yes")  # no stdin at all: must not prompt

    assert result.exit_code == 0
    out = output_of(result)
    assert RENAME_DIR_ID in out
    assert RENAME_TARGET_ID in out
    assert re.search(r"\bmigrated\b", out)


def test_non_interactive_without_yes_refuses_on_exit_two(archive: Path) -> None:
    """Criterion 16, matching ``remove``: no trustworthy answer exists in
    a pipe, so refuse up front naming the bypass — never hang, never act
    on an inherited 'y'."""
    before = tree_snapshot(archive)

    result = invoke_migrate(archive)  # no stdin, no --yes

    assert result.exit_code == 2
    assert "--yes" in output_of(result)
    assert tree_snapshot(archive) == before


def test_a_piped_yes_without_the_flag_still_refuses(archive: Path) -> None:
    before = tree_snapshot(archive)

    result = invoke_migrate(archive, stdin="y\n")

    assert result.exit_code == 2
    assert "--yes" in output_of(result)
    assert tree_snapshot(archive) == before


def test_ctrl_c_mid_migration_exits_130_and_prints_the_rerun_command(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 0007's contract: there is no resume state to lose, so the
    final line must be the paste-ready command that finishes the job —
    absolute archive path, and no ``--yes`` (the re-run earns its own
    preview)."""

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "rename", interrupt)

    result = invoke_migrate(archive, "--yes")

    assert result.exit_code == 130
    final_line = stdout_of(result).rstrip().splitlines()[-1]
    assert "migrate" in final_line
    assert str(archive.resolve()) in final_line
    assert "--yes" not in final_line


def test_a_migrated_archive_reports_nothing_to_do_and_exits_zero(archive: Path) -> None:
    """Criterion 13's no-op half, and the shape a cron line sees once the
    conversion is done."""
    first = invoke_migrate(archive, "--yes")
    assert first.exit_code == 0
    after_first = tree_snapshot(archive)

    result = invoke_migrate(archive, "--yes")

    assert result.exit_code == 0
    assert re.search(r"nothing to (do|migrate)|already migrated", output_of(result), re.IGNORECASE)
    assert tree_snapshot(archive) == after_first


def test_a_migrated_archive_is_a_no_op_under_plan_too(archive: Path) -> None:
    assert invoke_migrate(archive, "--yes").exit_code == 0

    result = invoke_migrate(archive, "--plan")

    assert result.exit_code == 0
    assert re.search(r"nothing to (do|migrate)|already migrated", output_of(result), re.IGNORECASE)


def test_a_collision_is_reported_by_plan_rather_than_discovered_mid_run(
    archive: Path,
) -> None:
    """Criterion 9: ``--plan`` prints "any collision that would block the
    run", so the human learns about it from the rehearsal, not from a
    half-finished conversion."""
    squatter = archive / TARGET_REL_PATH / Q8_REL
    squatter.parent.mkdir(parents=True)
    squatter.write_bytes(b"someone else's bytes, a different length entirely")

    result = invoke_migrate(archive, "--plan")

    assert result.exit_code != 0
    out = output_of(result)
    assert Q8_REL in out
    assert squatter.read_bytes().startswith(b"someone else's")
