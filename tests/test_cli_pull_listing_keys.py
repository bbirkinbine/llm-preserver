"""Keys versus patterns, and the two fallbacks — spec 0018.

Continues ``test_cli_pull_listing_tty.py``, whose harness these tests
import.

The prompt does double duty: it takes a glob pattern list, and it takes
single-character keys. Adjudication 5 settles the outer ambiguity with
**offered keys only** — a listing that fits shows no key line at all,
so every keystroke there is a pattern. *Inside* a windowed listing the
five reserved characters are always keys, and pressing one the frame
does not currently act on re-prompts with a one-line reason rather than
falling through as a glob (Brian's call, 2026-08-12, after the review
round measured what the fall-through cost). So each key needs two
tests: it works where it is offered, and it *re-prompts* where it is
not. The second half is the half that fails when a guard is deleted:

- ``b`` on the first expanded window has no history to pop. Without
  ``and back`` the implementation raises ``IndexError: pop from empty
  list``.
- ``m`` on the last window has nothing to advance to. Without
  ``and more`` it renders an empty frame footed ``showing 172-171 of
  171`` and loops. Before the re-prompt it returned the glob ``["m"]``,
  whose no-match error measured 210 physical rows on a 24-row
  terminal — a bigger wall than the listing this spec removes.
- ``s`` on a repo with no roll-up has nothing to return to. Without
  ``and offer_rollup`` it prints the unwindowed roll-up wall this spec
  exists to prevent.

The escape hatch is the comma: keys match the raw stripped answer
*before* the split, so ``f,`` is the pattern list ``["f"]``.

The fallbacks are adjudication 4's "one fallback, not two": the frame
chain is flat-fits → roll-up-fits → paged listing with ``s`` withheld.
Both ways of reaching the third rung are tested, because the two
conditions are separate terms in one boolean and a test of only the
first leaves the second free to be deleted — a repo whose roll-up
**says nothing shorter** (``collapses``) and one whose roll-up
**overflows in its own right** (``fits(rollup, ...)``). The first term
used to read `any("/" in path)`; live use found that shard-set
grouping made a flat 96-shard snapshot collapse to 14 lines while
still owning no directory, so the frame it most needed was withheld.
That case is pinned in ``test_pull_listing_shards.py``.
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


def test_b_on_the_first_expanded_window_re_prompts_and_says_why(monkeypatch, capsys):
    # There is no earlier window, so "b" is not in the key line. It must
    # re-prompt rather than pop an empty history (IndexError) or fall
    # through as the glob "b" (which matches nothing, and the no-match
    # error names the repo's files).
    patterns, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f", "b", "*.gguf"))

    assert patterns == ["*.gguf"]
    assert prompter.calls == 3
    assert "b = back a page" not in prompter.frames[1]
    assert "this is the first page — m, s, q, or type a pattern" in prompter.frames[2]
    # The frame is not reprinted: the note is one line, the key line is
    # directly above it.
    assert prompter.frames[2].count("showing ") == 0


def test_m_on_the_last_window_re_prompts_instead_of_walling(monkeypatch, capsys):
    # Seventeen frames advertised "m = more"; on the last one it is
    # withdrawn. Pressing it anyway used to return the glob ["m"], whose
    # no-match error measured 210 physical rows on a 24-row terminal —
    # a bigger wall than the listing this spec exists to remove.
    patterns, prompter = run_listing(
        monkeypatch, capsys, kimi_repo(), walk_all(final="m", after=["*.gguf"])
    )

    assert patterns == ["*.gguf"]
    assert "no further pages — b, s, q, or type a pattern" in prompter.frames[-1]
    footer = FOOTER_RE.search(prompter.frames[-2])
    assert (footer.group(2), footer.group(3)) == (str(TOTAL_FILES), str(TOTAL_FILES))


def test_s_without_a_rollup_to_return_to_re_prompts(monkeypatch, capsys):
    # A repo with no directories never had a roll-up frame; pressing "s"
    # must not conjure one, which on this repo is the 171-row wall.
    patterns, prompter = run_listing(monkeypatch, capsys, flat_root_repo(), scripted("s", "*.gguf"))

    assert patterns == ["*.gguf"]
    assert prompter.calls == 2
    assert "s = summary" not in prompter.frames[0]
    assert "no summary for this repo" in prompter.frames[1]


def test_a_reserved_key_that_does_nothing_on_the_rollup_re_prompts(monkeypatch, capsys):
    # m/b/s mean nothing before the listing is expanded.
    patterns, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("m", "*.gguf"))

    assert patterns == ["*.gguf"]
    assert prompter.calls == 2
    assert "press f to list the files first — f, q, or type a pattern" in prompter.frames[1]


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


def test_the_rollup_prompt_names_a_directory_the_frame_just_showed(monkeypatch, capsys):
    """The roll-up puts directory names on screen; the prompt shows one
    of them wrapped in the wildcards a pattern needs. Typing the bare
    name matches nothing, and the no-match error is the wall this
    listing work exists to remove."""
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("*.gguf"))

    assert "*UD-IQ1_M*" in prompter.texts[0]
    assert "*Q4_K_M*" not in prompter.texts[0]


def test_the_expanded_frames_keep_the_generic_example(monkeypatch, capsys):
    # The directory names are no longer summarised there, so a repo
    # directory would be an example of something not on screen.
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f", "*.gguf"))

    assert "*Q4_K_M*" in prompter.texts[1]
