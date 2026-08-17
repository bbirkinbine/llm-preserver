"""Recovery command on a doc-refresh planning stop — spec 0020.

A pull that stops during *planning* because an upstream documentation
file changed names ``--refresh-docs`` as the way out, and then withholds
the command that takes it. ``--refresh-docs`` is a ``pull`` flag; the
walk that reached the stop was a ``discover``; and the pull's shape —
repo id, archive path, the patterns typed at the file listing — exists
only in the scrollback the human would have to retype by hand.

These tests drive the real error path end to end: the discover handoff,
``--plan``, and a fully user-typed pull, each against an archive whose
README already differs in size from the hub's. The FakeHubClient from
conftest stands in for the hub; no network is touched.

Two output conventions matter here. Every assert reads
``click.unstyle``d text (rich ANSI renders in CI and not locally), and
stderr is read on its own stream: ``result.output`` already folds
stderr in, so counting lines through the shared ``combined_output``
helper would double-count every hint.

The mutation-proof guards (a changed weight, a message that merely
*mentions* the flag, a malformed repo id) and the composer's own unit
tests live in ``test_pull_doc_recovery_guards.py`` (300-line rule).
"""

import logging
import os
from collections.abc import Iterator

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.hub_discovery import ModelSummary

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
BASE_MODEL = "acme/tiny-chat"  # the repo's declared base; advisory only

# Three weights, each matched by exactly one of the patterns the walk
# types, so the composed command's three --include flags are
# unambiguous. The README publishes no hash (non-LFS), so a changed
# *size* is the hard stop this spec is about — the live trigger's exact
# shape (spec 0020: "recorded with size 5651 but the hub reports 5826").
ALPHA = "tiny-a.gguf"
BRAVO = "tiny-b.gguf"
CHARLIE = "tiny-c.gguf"
README_V1 = b"# tiny-chat card\n"
README_V2 = b"# tiny-chat card, revised upstream\n"

# Search pick 1 -> tree pick 0 (pull this repo) -> archive mode 1 (pick
# files) -> the three typed patterns. Planning stops before any
# confirmation, so the script ends at the patterns.
DISCOVER_STDIN = "1\n0\n1\n*a*,*b*,*c*\n"

# Spec 0020's lead-in, pinned here so one greppable string ties these
# tests to the implementation. It must not contain spec 0007's "to
# continue this pull later": test_resume_hint.py filters on that
# substring and asserts exactly one hint line in a dozen tests.
LEAD_IN = "to replace every changed documentation file and finish this pull"


def hub_files(readme: bytes) -> list[tuple[str, bytes, bool]]:
    """The repo's file list, parameterized by the README's contents."""
    return [
        (ALPHA, b"alpha weight bytes", True),
        (BRAVO, b"bravo weight bytes", True),
        (CHARLIE, b"charlie weight bytes", True),
        ("README.md", readme, False),
    ]


def stderr_text(result) -> str:
    """Stderr alone, unstyled — result.output folds stderr in."""
    return click.unstyle(result.stderr)


def stdout_text(result) -> str:
    """Stdout alone, unstyled."""
    return click.unstyle(result.stdout)


def recovery_lines(text: str) -> list[str]:
    """Every line carrying spec 0020's recovery-command lead-in."""
    return [line for line in text.splitlines() if LEAD_IN in line]


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    """Swap the CLI's hub-client seam for a fake (test_cli_pull.py pattern)."""
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def summary(repo_id: str, **overrides) -> ModelSummary:
    """A ModelSummary with all-None facts unless overridden."""
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def walk_client(fake_hub_factory, readme: bytes):
    """One search hit (the quant repo) whose tree offers pick 0 = pull."""
    return fake_hub_factory(
        files=hub_files(readme),
        search_results=[summary(REPO_ID, downloads=41, base_model=BASE_MODEL)],
        summaries={BASE_MODEL: summary(BASE_MODEL, downloads=999)},
    )


@pytest.fixture
def archived(tmp_path, monkeypatch, fake_hub_factory):
    """An archive holding one weight plus README v1 of the quant repo."""
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, walk_client(fake_hub_factory, README_V1))
    result = runner.invoke(app, ["pull", REPO_ID, str(archive), "--include", "*a*", "--yes"])
    assert result.exit_code == 0, click.unstyle(result.output)
    # setup_logging attached a handler bound to *this* invocation's
    # stderr; the CliRunner closes that stream, so leaving it in place
    # makes the next invoke's first log record print a logging traceback
    # into the output under test. conftest does the same teardown
    # between tests — this covers a second invoke inside one test.
    logging.getLogger("llm_preserver").handlers.clear()
    return archive


@pytest.fixture
def changed_hub(monkeypatch, fake_hub_factory):
    """The same repo, one upstream README revision later."""
    install_fake_hub(monkeypatch, walk_client(fake_hub_factory, README_V2))


@pytest.fixture
def restore_rust_log() -> Iterator[None]:
    """Snapshot and restore RUST_LOG, which --hf-logging writes.

    monkeypatch.delenv on an already-absent key records nothing to
    undo, so a RUST_LOG written by the code under test would survive
    teardown (test_hf_logging.py carries the same fixture).
    """
    before = os.environ.get("RUST_LOG")
    yield
    if before is None:
        os.environ.pop("RUST_LOG", None)
    else:
        os.environ["RUST_LOG"] = before


# --- the headline: the discover walk that motivated the spec ----------


def test_discover_doc_stop_prints_the_exact_recovery_command(archived, changed_hub):
    # Byte for byte, because the three-include expansion is the specific
    # thing a human retyping from scrollback gets wrong: the interactive
    # prompt splits on commas and the command line does not.
    result = runner.invoke(app, ["discover", "tiny", str(archived)], input=DISCOVER_STDIN)

    err = stderr_text(result)
    assert result.exit_code == 5, err
    assert "error [integrity]" in err  # the walk did reach the doc stop
    assert (
        f"{LEAD_IN}: llm-preserver pull {REPO_ID} {archived.resolve()} "
        "--include '*a*' --include '*b*' --include '*c*' --refresh-docs"
    ) in err.splitlines()


def test_plan_doc_stop_prints_the_command_for_the_real_pull(archived, changed_hub):
    # A rehearsal that hits the stop must be honest about how to
    # proceed — and the follow-up the human wants is the real pull, so
    # --plan never rides along (the 0006/0007 adjudication).
    result = runner.invoke(app, ["discover", "tiny", str(archived), "--plan"], input=DISCOVER_STDIN)

    err = stderr_text(result)
    assert result.exit_code == 5, err
    assert recovery_lines(err) == [
        f"{LEAD_IN}: llm-preserver pull {REPO_ID} {archived.resolve()} "
        "--include '*a*' --include '*b*' --include '*c*' --refresh-docs"
    ]


# --- a shape the human typed in full still gets the line ---------------


def test_a_fully_typed_pull_still_gets_the_recovery_command(archived, changed_hub):
    # Deliberately unlike spec 0007's hint, which is suppressed for a
    # user-typed shape because shell history already holds it. Here
    # what the human lacks is the flag, not the shape.
    result = runner.invoke(
        app,
        ["pull", REPO_ID, str(archived), "--include", "*a*", "--include", "*b*", "--yes"],
    )

    err = stderr_text(result)
    assert result.exit_code == 5, err
    assert recovery_lines(err) == [
        f"{LEAD_IN}: llm-preserver pull {REPO_ID} {archived.resolve()} "
        "--include '*a*' --include '*b*' --refresh-docs"
    ]


# --- where the line goes ----------------------------------------------


def test_the_recovery_command_is_the_line_after_the_error(archived, changed_hub):
    # Spec 0013's precedent (ollama_shape_hint): the recovery command
    # sits directly under the error it resolves, not somewhere in the
    # scrollback above it.
    result = runner.invoke(app, ["pull", REPO_ID, str(archived), "--include", "*a*", "--yes"])

    lines = stderr_text(result).splitlines()
    assert result.exit_code == 5, lines
    error_index = next(i for i, line in enumerate(lines) if line.startswith("error [integrity]"))
    after = lines[error_index + 1 :]
    assert after, "nothing follows the error line on stderr"
    assert LEAD_IN in after[0]


def test_stdout_stays_clean_of_the_recovery_command(archived, changed_hub):
    # A piped run's stdout is the data channel; diagnostics belong on
    # stderr.
    result = runner.invoke(app, ["pull", REPO_ID, str(archived), "--include", "*a*", "--yes"])

    out = stdout_text(result)
    assert result.exit_code == 5, out
    assert LEAD_IN in stderr_text(result)  # it printed — just not here
    assert LEAD_IN not in out
    assert "llm-preserver pull" not in out


# --- flags in effect ride along ---------------------------------------


def test_role_base_model_and_hf_logging_ride_along(archived, changed_hub, restore_rust_log):
    # The pasted command must reproduce the pull it replaces: curator
    # judgment (--role, --base-model) and the one diagnostic flag the
    # hint carries (--hf-logging, spec 0008) all survive.
    result = runner.invoke(
        app,
        [
            "pull",
            REPO_ID,
            str(archived),
            "--include",
            "*a*",
            "--role",
            "chat",
            "--base-model",
            BASE_MODEL,
            "--hf-logging",
            "--yes",
        ],
    )

    err = stderr_text(result)
    assert result.exit_code == 5, err
    hints = recovery_lines(err)
    assert len(hints) == 1, err
    assert "--role chat" in hints[0]
    assert f"--base-model {BASE_MODEL}" in hints[0]
    assert "--hf-logging" in hints[0]
    assert "--refresh-docs" in hints[0]
