"""Quitting a pull is exit 0, not a fault — spec 0021 (the file listing).

The tool's own principle is that exit codes name the fault domain, and a
human answering a question the tool asked is not a fault. ``remove``
already says so (a declined confirmation is exit 0, "nothing removed")
and ``discover``'s three quit prompts already return cleanly; ``pull``
is the one command that reported a fault for an answered question. This
module holds the **shared CLI harness** for that change plus the tests
for the file-listing prompt; ``test_cli_pull_decline_confirms.py``
imports the harness for the two confirmations.

Two terminals are simulated independently. ``CliRunner`` replaces
``sys.stdin`` and ``sys.stdout`` with ``_NamedTextIOWrapper`` instances
that differ only by ``name``, so ``isatty`` can be answered per stream —
which is what lets a test drive the case the spec turns on: stdout is a
terminal (spec 0018's wall verdict) while stdin is not (nobody is
typing). Patching ``sys.stdout.isatty`` directly does not work, because
the runner swaps the stream afterwards (spec 0010's hard-won fact,
``tests/test_cli_remove_guards.py:241``).

``LINES``/``COLUMNS`` are pinned because ``shutil.get_terminal_size``
consults them before asking the OS, so an inherited value on a CI runner
would otherwise decide which frame a listing opens on.
"""

import contextlib

import click
import typer.testing
from test_cli_discover_flow import quant_client, type_lines
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
QUIT_LINE = "nothing pulled: quit at the file listing"
DECLINE_PREFIX = "nothing pulled:"
COLUMNS = 80
ROWS = 24

# 40 short rows overflow an 80x24 screen, so this repo opens on the
# windowed frame spec 0018 built — the one that already offers ``q``.
WINDOWED_FILES = [(f"f{index:03d}.gguf", b"xxx", True) for index in range(40)]


def init_archive_dir(tmp_path, name="archive"):
    archive = tmp_path / name
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def simulate_streams(monkeypatch, *, stdin=True, stdout=True, columns=COLUMNS, rows=ROWS):
    """Choose which of the runner's streams claim to be a terminal."""
    verdicts = {"<stdin>": stdin, "<stdout>": stdout, "<stderr>": stdout}
    monkeypatch.setattr(
        typer.testing._NamedTextIOWrapper,
        "isatty",
        lambda self: verdicts.get(self.name, False),
    )
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))


def stdout_of(result) -> str:
    return click.unstyle(result.stdout)


def stderr_of(result) -> str:
    with contextlib.suppress(ValueError, AttributeError):
        return click.unstyle(result.stderr)
    return ""


def decline_line(result) -> str:
    """The one plain line a declined pull prints, on stdout.

    Asserting there is exactly one is half the criterion: a decline is
    one line, not a report, and it goes to stdout because it is not a
    diagnostic (``remove``'s precedent).
    """
    lines = [line for line in stdout_of(result).splitlines() if line.startswith(DECLINE_PREFIX)]
    assert len(lines) == 1, f"expected one {DECLINE_PREFIX!r} line on stdout, got {lines}"
    return lines[0]


def invoke_pull(archive, *args, stdin=None):
    return runner.invoke(app, ["pull", REPO_ID, str(archive), *args], input=stdin)


def test_quitting_a_fitting_listing_exits_0_with_one_plain_line(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())
    simulate_streams(monkeypatch)

    result = invoke_pull(archive, stdin="q\n")

    assert result.exit_code == 0
    assert decline_line(result) == QUIT_LINE
    assert stderr_of(result) == ""  # a decline is not a diagnostic
    assert list((archive / "models").iterdir()) == []


def test_quitting_a_windowed_listing_exits_0_the_same_way(tmp_path, monkeypatch, fake_hub_factory):
    # Spec 0018 built ``q`` into the paged frames at exit 2; it moves to
    # 0 with the rest, so one walk cannot end two ways.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(files=WINDOWED_FILES))
    simulate_streams(monkeypatch)

    result = invoke_pull(archive, stdin="q\n")

    assert result.exit_code == 0
    assert decline_line(result) == QUIT_LINE
    assert "showing 1-20 of 40" in stdout_of(result)  # it really was windowed
    assert list((archive / "models").iterdir()) == []


def test_q_quits_on_a_terminal_and_stays_a_pattern_on_a_pipe(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The pipe path gains nothing: no key line, so ``q`` is the glob it
    # always was and the existing no-match error stands.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    piped = invoke_pull(archive, stdin="q\n")
    simulate_streams(monkeypatch)
    attended = invoke_pull(archive, stdin="q\n")

    assert piped.exit_code == 2
    assert "match include patterns ['q']" in stderr_of(piped)
    assert attended.exit_code == 0
    assert decline_line(attended) == QUIT_LINE


def test_a_quit_prints_no_final_pull_line_that_a_completed_pull_prints(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Scrollback and scripts key on the final line (spec 0014), so the
    # run that pulled nothing must not carry the one that means it did.
    install_fake_hub(monkeypatch, fake_hub_factory())
    simulate_streams(monkeypatch)

    completed = invoke_pull(init_archive_dir(tmp_path, "done"), stdin="*Q4_K_M*\ny\n")
    quit_run = invoke_pull(init_archive_dir(tmp_path, "quit"), stdin="q\n")

    assert completed.exit_code == 0
    assert f"pulled {REPO_ID} into" in stdout_of(completed)
    assert quit_run.exit_code == 0
    assert decline_line(quit_run) == QUIT_LINE
    assert f"pulled {REPO_ID} into" not in stdout_of(quit_run)


def test_eof_at_the_file_listing_declines_on_a_terminal_but_stays_a_fault_on_a_pipe(
    tmp_path, monkeypatch, fake_hub_factory
):
    # TODO.md:39, pre-existing: today both die with click's bare
    # "Aborted." at exit 1 — the undocumented exit ``confirm_or_stop``'s
    # own docstring says a scripted pull must never hit.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    scripted = invoke_pull(archive, stdin="")
    simulate_streams(monkeypatch)
    attended = invoke_pull(archive, stdin="")

    assert attended.exit_code == 0
    assert decline_line(attended) == QUIT_LINE
    assert scripted.exit_code == 2
    assert "--include" in stderr_of(scripted)
    assert "--whole-repo" in stderr_of(scripted)


def test_the_listing_answerability_verdict_reads_stdin_not_stdout(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Answerability is a question about who is typing; spec 0018's
    # stdout verdict keeps its own job. The two must be able to
    # disagree: ``pull … < /dev/null`` on a terminal is a script, and a
    # keyboard feeding a redirected stdout is a human.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    simulate_streams(monkeypatch, stdin=True, stdout=False)
    typing_human = invoke_pull(archive, stdin="")
    simulate_streams(monkeypatch, stdin=False, stdout=True)
    redirected_stdin = invoke_pull(archive, stdin="")

    assert typing_human.exit_code == 0
    assert decline_line(typing_human) == QUIT_LINE
    assert redirected_stdin.exit_code == 2
    assert "--include" in stderr_of(redirected_stdin)


def test_quitting_under_plan_prints_no_plan_report_and_exits_0(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The listing prompt runs before the --plan branch, so ``q`` is
    # reachable under --plan and must not produce a dry-run report.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())
    simulate_streams(monkeypatch)

    reported = invoke_pull(archive, "--plan", stdin="*Q4_K_M*\n")
    quit_run = invoke_pull(archive, "--plan", stdin="q\n")

    assert reported.exit_code == 0
    assert "nothing downloaded" in stdout_of(reported)  # the plan's closing line
    assert quit_run.exit_code == 0
    assert decline_line(quit_run) == QUIT_LINE
    assert "nothing downloaded" not in stdout_of(quit_run)


def test_yes_does_not_answer_the_file_listing(tmp_path, monkeypatch, fake_hub_factory):
    # --yes auto-accepts the size confirmation only, so the listing is
    # still asked and ``q`` still ends the walk.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())
    simulate_streams(monkeypatch)

    result = invoke_pull(archive, "--yes", stdin="q\n")

    assert result.exit_code == 0
    assert decline_line(result) == QUIT_LINE
    assert list((archive / "models").iterdir()) == []


def test_a_discover_walk_that_quits_at_the_file_listing_exits_0(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The live trigger end to end: search pick, tree hop, archive mode,
    # then ``q``. The first three prompts advertise ``q = quit`` and
    # return 0; the fourth now agrees with them.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, quant_client(fake_hub_factory))
    simulate_streams(monkeypatch)

    result = runner.invoke(
        app, ["discover", "tiny", str(archive)], input=type_lines("1", "0", "1", "q")
    )

    assert result.exit_code == 0
    assert decline_line(result) == QUIT_LINE
    assert list((archive / "models").iterdir()) == []
