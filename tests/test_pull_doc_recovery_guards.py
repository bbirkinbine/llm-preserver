"""Guards on spec 0020's doc-refresh recovery command.

Split from test_pull_doc_recovery.py (300-line rule). Where that file
pins the line the human gets, this one pins the cases that must get
*nothing*, plus the composer's own contract.

Every guard here is written as a **differential** run — the case that
must print the line and the case that must not, in one test, against
the same archive. A bare "the line is absent" assertion passes today,
passes with the feature deleted, and passes with the feature broken;
pairing it with its control is what makes it fail for the right reason
now and keep meaning something later. That is spec 0017's lesson (a
remedy assertion satisfied by the substring inside *unmigrated*) and
spec 0018's (six load-bearing guards survived deletion, all green).

The three behaviors guarded:

- A changed **weight** must never be answered with ``--refresh-docs``.
  The doc line's whole value is that it is safe to paste without
  thinking; a payload-immutability stop (ADR 0001) has no paste-and-go
  way out, so it gets no command and the flag is not so much as named.
- Dispatch is **by exception type**. Two runs differing only in the
  exception class — identical message text, one of it containing the
  literal ``--refresh-docs`` — must differ in outcome. Re-implement the
  handler as a substring check on the message and this test fails.
- A repo id that fails validation yields **no line at all**. Shell
  quoting cannot defuse an argv token, so a hub-supplied id shaped like
  a flag must never become one on paste; no hint beats a booby-trapped
  one.

FakeHubClient / a raising seam double stand in for the hub; no network.
Output is ``click.unstyle``d. ``full_output`` here concatenates
``result.output`` and ``result.stderr``, which double-counts every
stderr line under this click version — safe because every assert in
this module is a substring test, never a line count
(``.claude/rules/python-code.md`` → "Asserting on CLI output"). Any
future count in this file must read ``click.unstyle(result.output)``
alone; ``test_pull_doc_recovery.py`` does exactly that.
"""

import logging

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.cli.resume_hint import RESUME_HINT_LEAD_IN
from llm_preserver.hub import PullIntegrityError

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
WEIGHT = "tiny-a.gguf"
README_V1 = b"# tiny-chat card\n"
README_V2 = b"# tiny-chat card, revised upstream\n"
WEIGHT_V1 = b"weight bytes v1"
WEIGHT_V2 = b"weight bytes v2, longer"

# One message text, raised as two different classes: the input to the
# dispatch guard. It names the flag on purpose.
SHARED_MESSAGE = (
    "README.md changed upstream; the archive is payload-immutable — "
    "re-run with --refresh-docs, which replaces every documentation file "
    "whose upstream content changed, not only this one"
)

# Same lead-in constant as test_pull_doc_recovery.py, deliberately
# duplicated rather than imported: a guard that imports the value it
# guards from the code under test cannot fail when that value moves.
LEAD_IN = "to replace every changed documentation file and finish this pull"


def hub_files(weight_bytes: bytes, readme: bytes) -> list[tuple[str, bytes, bool]]:
    """A one-weight repo plus its hash-less README."""
    return [(WEIGHT, weight_bytes, True), ("README.md", readme, False)]


def full_output(result) -> str:
    """Everything the run printed, unstyled (substring asserts only)."""
    return click.unstyle(result.output) + click.unstyle(result.stderr)


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


def invoke_pull(repo_id, archive, *extra):
    """Run one non-interactive pull, with a fresh log handler.

    setup_logging's handler binds to the invocation's stderr, which the
    CliRunner then closes; these tests invoke more than once, so the
    stale handler must go or the next run's first log record prints a
    logging traceback into the output under test.
    """
    logging.getLogger("llm_preserver").handlers.clear()
    return runner.invoke(app, ["pull", repo_id, str(archive), "--include", "*a*", "--yes", *extra])


class RaisingHubClient:
    """Hub-seam double whose repo_info raises the exception under test."""

    def __init__(self, exc):
        self._exc = exc

    def repo_info(self, repo_id):
        raise self._exc

    def download(self, repo_id, filename, revision, dest_dir):
        raise AssertionError("download must not be called after repo_info failed")


def doc_refresh_error(message: str):
    """The typed carrier spec 0020 adds, resolved at call time.

    Attribute access, not a module-level import: the class does not
    exist yet, and a missing name must fail the tests that need it
    rather than the collection of the whole file.
    """
    import llm_preserver.hub as hub

    return hub.PullDocRefreshError(message)


@pytest.fixture
def archived(tmp_path, monkeypatch, fake_hub_factory):
    """An archive holding the repo's single weight and its README."""
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(files=hub_files(WEIGHT_V1, README_V1)))
    result = invoke_pull(REPO_ID, archive)
    assert result.exit_code == 0, click.unstyle(result.output)
    return archive


# --- a changed weight is not a doc ------------------------------------


def test_only_a_changed_doc_gets_the_command_never_a_changed_weight(
    archived, monkeypatch, fake_hub_factory
):
    # The control run is load-bearing: without it this guard passes
    # with the whole feature deleted.
    install_fake_hub(monkeypatch, fake_hub_factory(files=hub_files(WEIGHT_V1, README_V2)))
    doc_stop = full_output(invoke_pull(REPO_ID, archived))

    install_fake_hub(monkeypatch, fake_hub_factory(files=hub_files(WEIGHT_V2, README_V1)))
    weight_stop = full_output(invoke_pull(REPO_ID, archived))

    assert LEAD_IN in doc_stop, doc_stop
    # The weight branch's own wording — proof the second run reached the
    # weight stop and not the doc one.
    assert "requires an explicit choice" in weight_stop
    assert LEAD_IN not in weight_stop
    # Not merely "no command": the flag is never suggested for payload.
    assert "--refresh-docs" not in weight_stop


# --- dispatch is by type, never by message text -----------------------


def test_dispatch_keys_on_the_exception_type_not_the_message_text(tmp_path, monkeypatch):
    # Two runs differing only in the exception class. Re-implement the
    # handler as `"--refresh-docs" in str(exc)` and the second run
    # starts printing the line, failing this test.
    archive = init_archive_dir(tmp_path)

    install_fake_hub(monkeypatch, RaisingHubClient(doc_refresh_error(SHARED_MESSAGE)))
    typed = full_output(invoke_pull(REPO_ID, archive))

    install_fake_hub(monkeypatch, RaisingHubClient(PullIntegrityError(SHARED_MESSAGE)))
    untyped = full_output(invoke_pull(REPO_ID, archive))

    assert "--refresh-docs" in untyped  # the message itself still says it
    assert LEAD_IN in typed, typed
    assert LEAD_IN not in untyped
    assert "llm-preserver pull" not in untyped


def test_the_typed_carrier_keeps_the_integrity_fault_domain(tmp_path, monkeypatch):
    # Exit code does not move: a doc-refresh stop is still integrity,
    # still exit 5. Only what the human is told changes.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, RaisingHubClient(doc_refresh_error(SHARED_MESSAGE)))

    result = invoke_pull(REPO_ID, archive)

    everything = full_output(result)
    assert result.exit_code == 5, everything
    assert "error [integrity]" in everything


# --- a repo id that fails validation composes nothing ------------------


def test_a_repo_id_not_shaped_like_one_gets_no_recovery_command(tmp_path, monkeypatch):
    # A hub-supplied id reaches the composer; one that is not shaped
    # like a repo id must yield no line rather than a quoted token that
    # parses as something else on paste. Same stop, same client, only
    # the id differs — so removing the validation fails this test.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, RaisingHubClient(doc_refresh_error(SHARED_MESSAGE)))

    valid = full_output(invoke_pull(REPO_ID, archive))
    malformed = full_output(invoke_pull("acme/tiny chat", archive))

    assert LEAD_IN in valid, valid
    assert "error [integrity]" in malformed  # the error itself still lands
    assert LEAD_IN not in malformed


# --- the composer's own contract --------------------------------------


def test_the_doc_refresh_hint_carries_exactly_one_refresh_docs_flag(tmp_path):
    from llm_preserver.cli.resume_hint import DOC_REFRESH_LEAD_IN, compose_doc_refresh_hint

    hint = compose_doc_refresh_hint(REPO_ID, tmp_path, include=["*a*", "*b*"])

    assert hint is not None
    assert hint.startswith(f"{DOC_REFRESH_LEAD_IN}: llm-preserver pull {REPO_ID} ")
    assert hint.count("--refresh-docs") == 1


def test_the_doc_refresh_hint_is_none_for_an_id_not_shaped_like_one(tmp_path):
    from llm_preserver.cli.resume_hint import compose_doc_refresh_hint

    assert compose_doc_refresh_hint("--yes", tmp_path, include=["*a*"]) is None
    assert compose_doc_refresh_hint("-rf", tmp_path, include=["*a*"]) is None
    assert compose_doc_refresh_hint("no-namespace", tmp_path, include=["*a*"]) is None
    # The three above are all refused by the "/" not in repo_id clause
    # alone, so on their own they pass with looks_like_repo_id deleted —
    # a tautology wearing a guard's name. These carry the mandatory
    # slash and must be refused by the shape check itself.
    assert compose_doc_refresh_hint("-x/y", tmp_path, include=["*a*"]) is None
    assert compose_doc_refresh_hint("owner/--yes", tmp_path, include=["*a*"]) is None
    assert compose_doc_refresh_hint("--refresh-docs/y", tmp_path, include=["*a*"]) is None
    assert compose_doc_refresh_hint("../y", tmp_path, include=["*a*"]) is None
    assert compose_doc_refresh_hint("a/../b", tmp_path, include=["*a*"]) is None


def test_the_doc_refresh_lead_in_cannot_be_read_as_the_resume_hint():
    # test_resume_hint.py's hint_lines() filters on the 0007 lead-in and
    # a dozen of its tests assert exactly one match. A doc lead-in
    # containing that substring would break them from across the suite.
    from llm_preserver.cli.resume_hint import DOC_REFRESH_LEAD_IN

    assert DOC_REFRESH_LEAD_IN == LEAD_IN
    assert RESUME_HINT_LEAD_IN not in DOC_REFRESH_LEAD_IN
