"""Keys versus patterns, and the two fallbacks — spec 0018.

Continues ``test_cli_pull_listing_tty.py``, whose harness these tests
import.

The prompt does double duty: it takes a glob pattern list, and it takes
single-character keys. Adjudication 5 settles the ambiguity with
**offered keys only** — a bare single character is a key exactly when
the frame on screen advertised it, and everything else is a pattern
list. So each key needs two tests, not one: it works where it is
offered, and it is *ordinary text* where it is not. The second half is
what the review round found missing, and it is the half that fails when
a guard is deleted:

- ``b`` on the first expanded window has no history to pop. Without
  ``and back`` the implementation raises ``IndexError: pop from empty
  list``; the contract is the pattern list ``["b"]``.
- ``m`` on the last window has nothing to advance to. Without
  ``and more`` it renders an empty frame footed ``showing 172-171 of
  171`` and loops; the contract is ``["m"]``.
- ``s`` on a repo with no roll-up has nothing to return to. Without
  ``and offer_rollup`` it prints the unwindowed roll-up wall this spec
  exists to prevent; the contract is ``["s"]``.

The fallbacks are adjudication 4's "one fallback, not two": the frame
chain is flat-fits → roll-up-fits → paged listing with ``s`` withheld.
Both ways of reaching the third rung are tested, because the two
conditions are separate terms in one boolean and a test of only the
first leaves the second free to be deleted — a repo with **no
directories** (``has_directories``) and a repo whose **roll-up
overflows in its own right** (``fits(rollup, ...)``).
"""

import io
import sys

import click
import pytest
import typer
from test_cli_pull_listing_tty import (
    FILES_PER_DIR,
    FIRST_WINDOW_KEYS,
    FOOTER_RE,
    MANY_DIRS,
    NO_ROLLUP_KEYS,
    REPO_ID,
    ROLLUP_KEYS,
    TOTAL_FILES,
    flat_root_repo,
    kimi_repo,
    many_dirs_repo,
    paths_in,
    run_listing,
    scripted,
    small_repo,
    walk_all,
)

from llm_preserver.cli.pull_exec.prompts import prompt_for_selection
from llm_preserver.hub import PullUserError

# --- a fitting listing offers no keys, so every character is a pattern --


def test_q_typed_on_a_fitting_listing_is_a_pattern_not_a_key(monkeypatch, capsys):
    # Adjudication 5: offered-keys-only means a repo with no wall keeps
    # every keystroke a pattern.
    patterns, _ = run_listing(monkeypatch, capsys, small_repo(), scripted("q"))

    assert patterns == ["q"]


# --- keys that are offered ----------------------------------------------


def test_surrounding_whitespace_still_reads_as_the_key(monkeypatch, capsys):
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted(" f ", "*.gguf"))

    assert prompter.calls == 2
    assert FIRST_WINDOW_KEYS in prompter.frames[1]


def test_a_pattern_typed_from_a_later_window_ends_the_stage(monkeypatch, capsys):
    # The human must not have to page back to a particular frame.
    patterns, prompter = run_listing(
        monkeypatch, capsys, kimi_repo(), scripted("f", "m", "m", "*Q6_K_XL*")
    )

    assert patterns == ["*Q6_K_XL*"]
    assert prompter.calls == 4


def test_q_on_an_overflowing_listing_aborts_the_pull_the_exit_2_way(monkeypatch, capsys):
    # PullUserError → exit 2, matching confirm_or_stop's posture that
    # user input is the exit-2 domain (spec 0018 scope correction).
    with pytest.raises(PullUserError):
        run_listing(monkeypatch, capsys, kimi_repo(), scripted("q"))


# --- keys that are not offered are ordinary text ------------------------


def test_b_on_the_first_expanded_window_is_a_pattern_not_a_key(monkeypatch, capsys):
    # There is no earlier window, so "b" is not in the key line and the
    # loop must read it as a glob rather than popping an empty history.
    patterns, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f", "b"))

    assert patterns == ["b"]
    assert prompter.calls == 2
    assert "b = back a page" not in prompter.frames[1]


def test_m_on_the_last_window_is_a_pattern_not_a_key(monkeypatch, capsys):
    # walk_all pages to the end, where the footer has withdrawn "m", and
    # then types it: nothing remains to advance to, so it is a glob.
    patterns, prompter = run_listing(monkeypatch, capsys, kimi_repo(), walk_all(final="m"))

    assert patterns == ["m"]
    last = FOOTER_RE.search(prompter.frames[-1])
    assert (last.group(2), last.group(3)) == (str(TOTAL_FILES), str(TOTAL_FILES))


def test_s_without_a_rollup_to_return_to_is_a_pattern_not_a_key(monkeypatch, capsys):
    # A repo with no directories never had a roll-up frame; pressing "s"
    # must not conjure one, which on this repo is the 171-row wall.
    patterns, prompter = run_listing(monkeypatch, capsys, flat_root_repo(), scripted("s"))

    assert patterns == ["s"]
    assert prompter.calls == 1
    assert "s = summary" not in prompter.frames[0]


# --- the comma escape hatch ---------------------------------------------


def test_a_comma_makes_a_rollup_key_a_pattern_list(monkeypatch, capsys):
    # Keys match the raw stripped input BEFORE the comma split.
    bare_comma, _ = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f,"))
    assert bare_comma == ["f"]

    with_pattern, _ = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f, *.gguf"))
    assert with_pattern == ["f", "*.gguf"]


def test_a_comma_makes_a_window_key_a_pattern_list_too(monkeypatch, capsys):
    # The same rule inside the expanded frame, where the offered keys
    # are different ones — the roll-up branch and the window branch each
    # match the key themselves, so each needs its own disambiguation.
    bare_comma, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f", "m,"))
    assert bare_comma == ["m"]
    assert prompter.calls == 2

    with_pattern, prompter = run_listing(
        monkeypatch, capsys, kimi_repo(), scripted("f", "m, *.gguf")
    )
    assert with_pattern == ["m", "*.gguf"]
    assert prompter.calls == 2


# --- the fallbacks ------------------------------------------------------


def test_a_repo_with_no_directories_opens_on_the_paged_listing(monkeypatch, capsys):
    # Adjudication 4, first arm: with no directory line to show, the
    # roll-up frame does not exist, so neither does "s".
    info = flat_root_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("*.gguf"))

    opening = prompter.frames[0]
    assert FOOTER_RE.search(opening).group(3) == str(TOTAL_FILES)
    assert paths_in(opening, info)[0] == info.files[0].path
    assert NO_ROLLUP_KEYS in opening
    assert "s = summary" not in opening
    assert ROLLUP_KEYS not in opening


def test_a_rollup_too_tall_for_the_screen_falls_through_to_the_paged_listing(monkeypatch, capsys):
    # Adjudication 4, second arm: 200 directories summarize to 200 lines,
    # which is a wall of its own. Same rule, same landing — the paged
    # listing with "s" withheld, because there is no frame to go back to.
    info = many_dirs_repo()
    total = MANY_DIRS * FILES_PER_DIR

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("*.gguf"))

    opening = prompter.frames[0]
    assert FOOTER_RE.search(opening).group(3) == str(total)
    assert paths_in(opening, info)[0] == info.files[0].path
    assert NO_ROLLUP_KEYS in opening
    assert "s = summary" not in opening
    assert ROLLUP_KEYS not in opening
    # And it really is the per-file listing, not a roll-up: a directory
    # line for these groups would read "2 files".
    assert "2 files" not in opening


def test_s_is_withheld_on_every_window_of_an_overflowing_rollup(monkeypatch, capsys):
    # The key is withheld for the whole walk, not only on the frame the
    # fallback opened on.
    _, prompter = run_listing(monkeypatch, capsys, many_dirs_repo(), walk_all(), limit=250)

    assert prompter.calls > 1, "the walk never paged"
    for frame in prompter.frames:
        assert "s = summary" not in frame


# --- the non-TTY streams ------------------------------------------------


def test_piped_output_is_the_flat_listing_byte_for_byte(monkeypatch, capsys):
    # The criterion that keeps every existing prompt_for_selection test
    # valid: a pipe gets no roll-up, no window, no footer, no key line —
    # a pipe has no scroll problem, it has a file.
    from llm_preserver.cli.pull_exec.listing import flat_header, flat_lines

    info = kimi_repo()

    patterns, prompter = run_listing(monkeypatch, capsys, info, scripted("*.gguf"), tty=False)

    assert patterns == ["*.gguf"]
    assert prompter.calls == 1
    expected = "\n".join([flat_header(REPO_ID), *flat_lines(info.files)]) + "\n"
    assert prompter.frames[0] == expected


def test_a_degraded_stdout_prints_the_flat_listing_instead_of_raising(monkeypatch):
    # The 0011/0012 class: a detached or closed stdout must resolve to
    # the non-TTY path, never a traceback. Nothing is patched at the
    # resolver seam here — the real is_interactive does the guarding.
    from llm_preserver.cli.pull_exec.listing import flat_header, flat_lines

    class BrokenTtyStream(io.StringIO):
        def isatty(self):
            raise ValueError("I/O operation on closed file.")

    stream = BrokenTtyStream()
    info = kimi_repo()
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "*.gguf")
    monkeypatch.setattr(sys, "stdout", stream)

    patterns = prompt_for_selection(info, REPO_ID)

    printed = click.unstyle(stream.getvalue())
    monkeypatch.undo()
    assert patterns == ["*.gguf"]
    assert printed == "\n".join([flat_header(REPO_ID), *flat_lines(info.files)]) + "\n"
