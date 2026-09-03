"""``q`` on a listing that fits — spec 0021.

Continues ``test_cli_pull_listing_tty.py``, whose harness these tests
import.

Spec 0018's **offered keys only** rule held literally on the frame that
shipped before it: a listing small enough to fit printed no key line, so
every keystroke there was a glob — including ``q``, three prompts after
``discover`` taught it. The rule is right and stays; what changes is
that ``q`` is now *offered* here, which is what makes it a key.

Only ``q``. ``m``, ``b``, ``s`` and ``f`` are frame-local — there is no
page to advance to, none to go back to, no summary to return to, and no
listing to expand — so they stay patterns, and the fits frame must keep
asking through ``_ask`` rather than the windowed loop's key-matching
prompt. The escape hatch for a repo that owns a file called ``q`` is the
one already documented: keys match the whole answer before the comma
split, so ``q,`` is the pattern list ``["q"]``.

``PullDeclined`` is imported inside each test body, following
``conftest``'s rule that collection of unrelated tests must never depend
on an implementation module existing.
"""

import pytest
import typer
from test_cli_pull_listing_tty import info_for, run_listing, scripted, small_repo

from llm_preserver.cli.pull_exec.listing import window_keys

# The key line a fitting listing gains: exactly what ``window_keys``
# renders with nothing else active, so the promise a fits frame makes
# and the one a windowed frame makes cannot drift apart.
FLAT_KEYS = "q = quit"

QUIT_LINE = "nothing pulled: quit at the file listing"


def test_a_fitting_listing_offers_quit_as_its_last_line(monkeypatch, capsys):
    patterns, prompter = run_listing(monkeypatch, capsys, small_repo(), scripted("*Q4_K_M*"))

    assert patterns == ["*Q4_K_M*"]
    assert prompter.calls == 1
    assert prompter.frames[0].splitlines()[-1] == FLAT_KEYS
    assert window_keys(more=False, back=False, summary=False) == FLAT_KEYS


def test_q_at_a_fitting_listing_quits_instead_of_naming_a_file(monkeypatch, capsys):
    # The live trigger (2026-09-03): 29 files, a tall terminal, and a
    # bare "q" that matched nothing and printed the available-files wall.
    from llm_preserver.pull_decline import PullDeclined

    with pytest.raises(PullDeclined) as excinfo:
        run_listing(monkeypatch, capsys, small_repo(), scripted("q"))

    assert str(excinfo.value) == QUIT_LINE


def test_only_q_becomes_a_key_on_a_fitting_listing(monkeypatch, capsys):
    # Both halves matter. If the fits frame kept asking through the
    # plain prompt, the q leg fails; if it switched to the windowed
    # loop's key matching, every other reserved key would re-prompt and
    # ``scripted`` would run out of answers.
    from llm_preserver.pull_decline import PullDeclined

    for key in ("f", "m", "b", "s"):
        patterns, _ = run_listing(monkeypatch, capsys, small_repo(), scripted(key))
        assert patterns == [key], f"{key!r} stopped being a pattern on a frame that offers no keys"

    with pytest.raises(PullDeclined):
        run_listing(monkeypatch, capsys, small_repo(), scripted("q"))


def test_surrounding_whitespace_still_reads_as_the_quit_key(monkeypatch, capsys):
    # The windowed frames pin this for ``f`` already. Without it here,
    # dropping ``.strip()`` from the fits frame's key match leaves the
    # whole suite green while a trailing space turns the advertised quit
    # key back into the no-match wall this spec exists to remove
    # (review round, 2026-09-03).
    from llm_preserver.pull_decline import PullDeclined

    for typed in (" q", "q ", "  q  "):
        with pytest.raises(PullDeclined):
            run_listing(monkeypatch, capsys, small_repo(), scripted(typed))


def test_a_file_named_q_is_still_archivable_through_the_comma(monkeypatch, capsys):
    # The fits frame is where a one-character repo path is least
    # improbable, so the escape hatch is pinned here.
    from llm_preserver.pull_decline import PullDeclined

    repo = info_for([("q", 1024), ("README.md", 512)])

    escaped, _ = run_listing(monkeypatch, capsys, repo, scripted("q,"))
    assert escaped == ["q"]

    with pytest.raises(PullDeclined):
        run_listing(monkeypatch, capsys, repo, scripted("q"))


def test_q_quits_on_a_terminal_and_stays_a_pattern_on_a_pipe(monkeypatch, capsys):
    # A pipe offers no keys, so it gains nothing: same repo, same
    # answer, two outcomes, and the pipe's is the one it has today.
    from llm_preserver.pull_decline import PullDeclined

    piped, prompter = run_listing(monkeypatch, capsys, small_repo(), scripted("q"), tty=False)
    assert piped == ["q"]
    assert FLAT_KEYS not in prompter.frames[0]

    with pytest.raises(PullDeclined):
        run_listing(monkeypatch, capsys, small_repo(), scripted("q"))


def test_eof_at_a_fitting_listing_declines_on_a_terminal_but_faults_on_a_pipe(monkeypatch, capsys):
    # TODO.md:39, pre-existing: click raises Abort for both Ctrl-D and
    # an exhausted pipe, and neither was caught. The interactive verdict
    # is what tells a human who answered from a script that could not.
    from llm_preserver.hub import PullUserError
    from llm_preserver.pull_decline import PullDeclined

    def eof(_frames):
        raise typer.Abort

    with pytest.raises(PullDeclined) as declined:
        run_listing(monkeypatch, capsys, small_repo(), eof)

    with pytest.raises(PullUserError) as fault:
        run_listing(monkeypatch, capsys, small_repo(), eof, tty=False)

    assert str(declined.value) == QUIT_LINE
    assert "--include" in str(fault.value)
    assert "--whole-repo" in str(fault.value)
