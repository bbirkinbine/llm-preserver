"""No frame exceeds one screen, at any terminal size — spec 0018.

Continues ``test_cli_pull_listing_tty.py``, whose harness these tests
import. This is the spec's headline criterion and 0015's hardest-won
one: a row wider than the terminal is charged the rows it wraps to, and
so is the chrome — the header, the footer, the key line, and click's
rendered prompt.

**A single geometry cannot test it.** The review round measured the
defect that proves this: the frame budget charged the footer at
``showing 1-171 of 171 — more (m) · back (b)``, 42 characters, while the
footer a paging human actually reads is ``showing 100-105 of 171 …``, 44
characters. At 42 columns the first fits one row and the second wraps to
two, so every later frame rendered 25 physical rows on a 24-row screen.
At 80 columns both are one row and the bug is invisible — which is
exactly why the one-screen test that was hardwired to 80 columns could
not fail. The same blindness hid a missing prompt charge in ``_chrome``:
free at 80, an overrun at 60.

So the criterion is swept rather than sampled. Both frame chains are
walked at every width from 30 to 120 — the repo that opens on a roll-up
and the one with no directories that opens straight onto the paged
listing — and the height dimension is swept at the width where the
footer defect lived. Roughly a hundred and fifty thousand frames get
measured; it costs seconds, because nothing here touches a network, a
disk, or a real terminal.

Spec 0021 adds one line to the fits frame's chrome — ``q = quit``, now
offered where a listing needs no window — and the same rule applies to
it: a listing that fits only *without* that line must take the windowed
path rather than print one physical row over budget. The boundary is
swept rather than sampled for the same reason as everything else here,
and the sweep is built from the budget arithmetic rather than a
hardwired file count, so it finds the boundary at every geometry instead
of at one.

The one documented exemption is 0015's escape hatch: on a screen too
short for its own chrome, ``resolve_window_size`` floors the budget at
``MIN_WINDOW_ROWS`` rather than returning a window of zero rows, because
a window of zero rows means ``m`` can never advance. Such a frame
overruns by design. The sweeps stay above that band and the floor gets
its own test, which asserts the property the floor exists to buy —
forward progress — rather than a height it cannot meet.
"""

from itertools import pairwise

import pytest
from test_cli_pull_listing_tty import (
    COLUMNS,
    FOOTER_RE,
    REPO_ID,
    ROWS,
    flat_root_repo,
    info_for,
    kimi_repo,
    paths_in,
    physical_height,
    prompt_cost,
    run_listing,
    scripted,
    walk_all,
)

from llm_preserver.cli.pull_exec.listing import flat_header
from llm_preserver.cli.window import MIN_WINDOW_ROWS
from llm_preserver.text_window import wrapped_height

# The sweep bounds. 30 columns is narrower than any terminal anyone
# uses; 120 is wider than the widest row these repos render, so the
# no-wrap case is covered too.
NARROWEST = 30
WIDEST = 120

# 42 columns is where both reviewers measured the footer overrun.
DEFECT_COLUMNS = 42

# Below this the chrome alone exceeds the screen and MIN_WINDOW_ROWS
# floors the budget — the documented escape hatch, tested separately.
SHORTEST_UNFLOORED = 14
TALLEST = 60

# Frames per walk stay well under this; the cap only stops a runaway.
FRAME_CAP = 400

SHAPES = {
    "kimi (roll-up, then paged)": kimi_repo,
    "flat root (paged from the first frame)": flat_root_repo,
}


def tallest_frame(monkeypatch, capsys, info, *, columns: int, rows: int) -> int:
    """Walk every frame of a listing and return the worst physical height.

    The prompt is charged on top of the captured frame: click has not
    rendered it when the frame is read, but the human sees it on the
    same screen. Each frame is charged **the prompt it actually
    asked**, not the generic one — the roll-up names one of the repo's
    own directories in its example, so it is the wider prompt, and
    charging the constant measures a frame the human never sees.
    Mutating ``chrome_lines`` to ignore its ``prompt`` argument left
    this whole sweep green while the real roll-up overflowed by a row
    at 38x29, 76x16 and 77x16 (review round, 2026-09-03).
    """
    capsys.readouterr()
    _, prompter = run_listing(
        monkeypatch,
        capsys,
        info,
        walk_all(),
        columns=columns,
        rows=rows,
        limit=FRAME_CAP,
    )
    assert prompter.calls > 1, f"nothing paged at {columns}x{rows}"
    return max(
        physical_height(frame, columns) + wrapped_height(f"{asked}: ", columns)
        for frame, asked in zip(prompter.frames, prompter.texts, strict=True)
    )


@pytest.mark.parametrize("columns", range(NARROWEST, WIDEST + 1))
def test_no_frame_exceeds_one_screen_at_any_terminal_width(monkeypatch, capsys, columns):
    for name, build in SHAPES.items():
        tallest = tallest_frame(monkeypatch, capsys, build(), columns=columns, rows=ROWS)

        assert tallest <= ROWS, f"{name} at {columns}x{ROWS}: a frame filled {tallest} rows"


# A cross through two axes cannot reach a defect that sits off both of
# them. These three came out of a full 30-120 x 14-60 grid run against a
# ``chrome_lines`` that ignored its prompt argument: none lies on
# rows=ROWS or columns=DEFECT_COLUMNS, so the two sweeps below miss all
# three. The grid itself is ~25s, too slow for the suite; its findings
# are cheap to keep (review round, 2026-09-03).
OFF_AXIS = [(38, 29), (76, 16), (77, 16)]


@pytest.mark.parametrize(("columns", "rows"), OFF_AXIS)
def test_no_frame_exceeds_one_screen_off_the_swept_axes(monkeypatch, capsys, columns, rows):
    tallest = tallest_frame(monkeypatch, capsys, kimi_repo(), columns=columns, rows=rows)

    assert tallest <= rows, f"at {columns}x{rows}: a frame filled {tallest} rows"


@pytest.mark.parametrize("rows", range(SHORTEST_UNFLOORED, TALLEST + 1))
def test_no_frame_exceeds_one_screen_at_any_terminal_height(monkeypatch, capsys, rows):
    tallest = tallest_frame(monkeypatch, capsys, kimi_repo(), columns=DEFECT_COLUMNS, rows=rows)

    assert tallest <= rows, f"at {DEFECT_COLUMNS}x{rows}: a frame filled {tallest} rows"


def test_the_swept_frames_really_do_wrap_or_the_sweep_proves_nothing(monkeypatch, capsys):
    # A listing whose rows all fit the terminal would satisfy the sweep
    # while charging nothing for wrapping. The reproduction repo's shard
    # rows are 82 characters, so they wrap at every width up to 81.
    _, prompter = run_listing(monkeypatch, capsys, kimi_repo(), walk_all())

    assert any(len(line) > COLUMNS for frame in prompter.frames for line in frame.splitlines()), (
        "no rendered line was wide enough to wrap"
    )


def test_a_screen_too_short_for_its_chrome_still_pages_forward(monkeypatch, capsys):
    # 0015's documented escape hatch. The budget floors at
    # MIN_WINDOW_ROWS instead of collapsing to zero, so such a frame does
    # overrun the screen — what must hold is that "m" keeps making
    # progress and the walk terminates having shown every file.
    info = info_for([(f"quant/shard-{index:05d}.gguf", 1024) for index in range(1, 41)])

    _, prompter = run_listing(
        monkeypatch, capsys, info, walk_all(), columns=NARROWEST, rows=8, limit=FRAME_CAP
    )

    windows = [frame for frame in prompter.frames if FOOTER_RE.search(frame)]
    assert len(windows) > 1, "the floored budget rendered a single frame"
    for window in windows:
        assert paths_in(window, info), "a floored window rendered no rows at all"
    seen = [path for window in windows for path in paths_in(window, info)]
    assert seen == [repo_file.path for repo_file in info.files]


def test_a_floored_budget_never_advances_by_less_than_one_row(monkeypatch, capsys):
    # The reason the floor exists: a window that fits zero rows makes "m"
    # a no-op and the listing unreachable. Every step must move at least
    # one file forward.
    info = info_for([(f"quant/shard-{index:05d}.gguf", 1024) for index in range(1, 41)])

    _, prompter = run_listing(
        monkeypatch, capsys, info, walk_all(), columns=NARROWEST, rows=8, limit=FRAME_CAP
    )

    starts = [
        int(FOOTER_RE.search(frame).group(1))
        for frame in prompter.frames
        if FOOTER_RE.search(frame)
    ]
    assert all(later > earlier for earlier, later in pairwise(starts))


# --- the fits frame's own chrome (spec 0021) ----------------------------

# The key line a fitting listing now prints, charged before the frame is
# sized. Verbatim from ``window_keys`` with nothing else offered.
FLAT_KEYS = "q = quit"


def short_row_repo(count: int):
    """``count`` files whose rows are one physical line at any swept width."""
    return info_for([(f"f{index:03d}.gguf", 1024) for index in range(count)])


def flat_budget(columns: int, rows: int, *, key_line: bool) -> int:
    """Rows a fitting listing may spend, with and without the key line."""
    chrome = wrapped_height(flat_header(REPO_ID), columns) + prompt_cost(columns)
    if key_line:
        chrome += wrapped_height(FLAT_KEYS, columns)
    return max(MIN_WINDOW_ROWS, rows - chrome)


def first_frame(monkeypatch, capsys, count: int, *, columns: int, rows: int) -> str:
    """The frame a repo of ``count`` short files opens on."""
    _, prompter = run_listing(
        monkeypatch,
        capsys,
        short_row_repo(count),
        scripted("*.gguf"),
        columns=columns,
        rows=rows,
        limit=FRAME_CAP,
    )
    return prompter.frames[0]


def assert_the_key_line_decides(monkeypatch, capsys, *, columns: int, rows: int) -> None:
    """One row either side of the boundary the key line moves."""
    charged = flat_budget(columns, rows, key_line=True)
    uncharged = flat_budget(columns, rows, key_line=False)
    assert uncharged == charged + 1, f"the key line cost no row at {columns}x{rows}"

    over = first_frame(monkeypatch, capsys, uncharged, columns=columns, rows=rows)
    assert "showing " in over, (
        f"{uncharged} rows printed flat at {columns}x{rows}: the key line was not charged"
    )

    fitting = first_frame(monkeypatch, capsys, charged, columns=columns, rows=rows)
    assert "showing " not in fitting, f"{charged} rows stopped fitting at {columns}x{rows}"
    assert fitting.splitlines()[-1] == FLAT_KEYS


def test_a_repo_that_fits_only_without_the_key_line_takes_the_windowed_path(monkeypatch, capsys):
    # The documented boundary: at 80x24 the fits frame charged header +
    # prompt = 2 rows, so 22 short rows fit. Charging "q = quit" makes
    # the budget 21, and the 22-row repo must window rather than print
    # one physical row over budget.
    assert flat_budget(COLUMNS, ROWS, key_line=False) == 22
    assert flat_budget(COLUMNS, ROWS, key_line=True) == 21

    assert_the_key_line_decides(monkeypatch, capsys, columns=COLUMNS, rows=ROWS)


@pytest.mark.parametrize("columns", range(NARROWEST, WIDEST + 1))
def test_the_key_line_is_charged_before_the_fits_check_at_any_width(monkeypatch, capsys, columns):
    # Spec 0018's lesson: a frame-sizing test pinned to one terminal
    # width cannot fail. The header and the prompt wrap at different
    # widths, so the boundary lands at a different file count on each.
    assert_the_key_line_decides(monkeypatch, capsys, columns=columns, rows=ROWS)


@pytest.mark.parametrize("rows", range(SHORTEST_UNFLOORED, TALLEST + 1))
def test_the_key_line_is_charged_before_the_fits_check_at_any_height(monkeypatch, capsys, rows):
    assert_the_key_line_decides(monkeypatch, capsys, columns=DEFECT_COLUMNS, rows=rows)
