"""Paging the expanded file listing — spec 0018, ``f``/``m``/``b``/``s``.

Continues ``test_cli_pull_listing_tty.py``, whose harness these tests
import. What is pinned here is the walk itself: which rows a window
shows, where the next one resumes, where ``b`` lands, that ``s`` and
``f`` are a toggle that keeps your place, and the footer grammar that
tells the human all of it.

Two of these are the spec-0017 lesson applied — a guard that passes
with the code it names deleted is not a guard:

- **``b`` must return to the window before this one, not to the first.**
  A script of ``f, m, b`` cannot tell the difference, because the
  previous window *is* window one; ``start = history.pop()`` and
  ``start = 0`` both pass it. The scripts here go ``f, m, m, b`` so the
  landing frame is the middle window (measured footers: ``1-11``,
  ``12-21``, ``22-31``, then ``12-21``).
- **``f`` from the roll-up must not reset the offset.** The spec calls
  the two frames a toggle, and a toggle that dumps you back at page one
  costs eight keypresses to undo (review round, 2026-08-12). Pinned by
  walking out to window three, pressing ``s``, then ``f``.

Completeness is the guard the abandoned grouping spec failed: the
roll-up summarizes, and ``f`` still reaches every single file, in hub
order, in the same row shape a fitting flat listing prints.
"""

from test_cli_pull_listing_tty import (
    BACK,
    FIRST_WINDOW_KEYS,
    FOOTER_RE,
    LATER_WINDOW_KEYS,
    MODEL,
    MORE,
    QUANT_DIRS,
    REPO_ID,
    ROLLUP_KEYS,
    TOTAL_FILES,
    kimi_repo,
    paths_in,
    run_listing,
    scripted,
    walk_all,
)


def test_the_rollup_frame_annotates_a_root_companion_file(monkeypatch, capsys):
    # Root files list individually in the roll-up, so they keep the kind
    # notes COMPANION_RULES gives them; only directory lines go bare.
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("*.gguf"))

    assert "mmproj-BF16.gguf  — vision projector" in prompter.frames[0]


def test_f_expands_the_rollup_into_the_first_window_of_files(monkeypatch, capsys):
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("f", "*.gguf"))

    window = prompter.frames[1]
    # Either header form; the spec pins the footer and keys, not this.
    assert window.splitlines()[0].startswith(f"files in {REPO_ID}")
    assert paths_in(window, info)[0] == info.files[0].path
    assert FOOTER_RE.search(window) is not None
    assert FIRST_WINDOW_KEYS in window


def test_the_expanded_windows_are_the_flat_listing_in_hub_order(monkeypatch, capsys):
    # info.files order verbatim across the whole walk, in the same row
    # shape a fitting flat listing prints — kind notes included.
    from llm_preserver.cli.pull_exec.listing import flat_lines

    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, walk_all())

    rows = flat_lines(info.files)
    listed = [
        line for window in prompter.frames[1:] for line in window.splitlines() if line in rows
    ]
    assert listed == rows


def test_m_shows_the_next_window_repeating_no_row(monkeypatch, capsys):
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("f", "m", "*.gguf"))

    first, second = paths_in(prompter.frames[1], info), paths_in(prompter.frames[2], info)
    assert second, "m rendered no rows"
    assert not set(first) & set(second)


def test_b_returns_to_the_rows_of_the_previous_window(monkeypatch, capsys):
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("f", "m", "m", "b", "*.gguf"))

    assert paths_in(prompter.frames[4], info) == paths_in(prompter.frames[2], info)


def test_b_from_the_third_window_does_not_land_back_on_the_first(monkeypatch, capsys):
    # The mutation this kills: `start = history.pop()` → `start = 0`,
    # which a `f, m, b` script cannot see because window one IS the
    # previous window there.
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("f", "m", "m", "b", "*.gguf"))

    landed = FOOTER_RE.search(prompter.frames[4])
    assert (landed.group(1), landed.group(2)) == ("12", "21")
    assert paths_in(prompter.frames[4], info) != paths_in(prompter.frames[1], info)
    assert BACK in prompter.frames[4], "b landed somewhere with no earlier window"


def test_s_reprints_the_rollup_so_neither_frame_is_a_dead_end(monkeypatch, capsys):
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, scripted("f", "s", "*.gguf"))

    back_at_rollup = prompter.frames[2]
    assert ROLLUP_KEYS in back_at_rollup
    for directory in QUANT_DIRS:
        assert f"{directory}/" in back_at_rollup
    assert f"{MODEL}-UD-Q4_K_XL-00001" not in back_at_rollup


def test_f_after_s_returns_to_the_window_you_left(monkeypatch, capsys):
    # The toggle keeps your place: three windows in, a round trip through
    # the roll-up must land on window three again, with b still offered.
    info = kimi_repo()

    _, prompter = run_listing(
        monkeypatch, capsys, info, scripted("f", "m", "m", "s", "f", "*.gguf")
    )

    left, returned = prompter.frames[3], prompter.frames[5]
    assert FOOTER_RE.search(returned).groups() == FOOTER_RE.search(left).groups()
    assert paths_in(returned, info) == paths_in(left, info)
    assert BACK in returned, "the return trip lost the history b steps through"


def test_paging_reaches_every_file_and_repeats_none_within_a_window(monkeypatch, capsys):
    # The completeness guard the abandoned grouping spec failed: the
    # roll-up summarizes, and f still reaches every single file.
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, walk_all())

    seen: list[str] = []
    for window in prompter.frames[1:]:
        listed = paths_in(window, info)
        assert len(listed) == len(set(listed))
        for path in listed:
            assert window.count(path) == 1
        seen += listed
    assert seen == [repo_file.path for repo_file in info.files]


# --- footer grammar -----------------------------------------------------


def test_the_first_window_offers_more_and_counts_the_whole_file_list(monkeypatch, capsys):
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f", "*.gguf"))

    window = prompter.frames[1]
    footer = FOOTER_RE.search(window)
    assert footer.group(1) == "1"
    assert footer.group(3) == str(TOTAL_FILES)  # a true total, not a high-water mark
    assert MORE in window
    assert BACK not in window


def test_a_later_window_offers_back_and_resumes_where_the_last_one_stopped(monkeypatch, capsys):
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), scripted("f", "m", "*.gguf"))

    first = FOOTER_RE.search(prompter.frames[1])
    second = FOOTER_RE.search(prompter.frames[2])
    assert int(second.group(1)) == int(first.group(2)) + 1
    assert BACK in prompter.frames[2]
    assert LATER_WINDOW_KEYS in prompter.frames[2]


def test_the_last_window_withdraws_more_from_the_footer_and_the_keys(monkeypatch, capsys):
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), walk_all())

    last = prompter.frames[-1]
    footer = FOOTER_RE.search(last)
    assert (footer.group(2), footer.group(3)) == (str(TOTAL_FILES), str(TOTAL_FILES))
    assert MORE not in last
    assert "m = more" not in last
    assert BACK in last


def test_no_frame_ever_shows_a_row_range_past_the_end_of_the_list(monkeypatch, capsys):
    # An empty frame footed "showing 172-171 of 171" is what an
    # unguarded "m" on the last window renders; the footer arithmetic is
    # asserted over the whole walk so no frame can claim rows that do
    # not exist.
    info = kimi_repo()

    _, prompter = run_listing(monkeypatch, capsys, info, walk_all())

    for window in prompter.frames[1:]:
        first, last, total = (int(group) for group in FOOTER_RE.search(window).groups())
        assert total == TOTAL_FILES
        assert 1 <= first <= last <= TOTAL_FILES
        assert len(paths_in(window, info)) == last - first + 1
