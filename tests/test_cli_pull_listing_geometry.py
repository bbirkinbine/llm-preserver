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
    ROWS,
    flat_root_repo,
    info_for,
    kimi_repo,
    paths_in,
    physical_height,
    prompt_cost,
    run_listing,
    walk_all,
)

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
    same screen.
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
    return max(physical_height(frame, columns) + prompt_cost(columns) for frame in prompter.frames)


@pytest.mark.parametrize("columns", range(NARROWEST, WIDEST + 1))
def test_no_frame_exceeds_one_screen_at_any_terminal_width(monkeypatch, capsys, columns):
    for name, build in SHAPES.items():
        tallest = tallest_frame(monkeypatch, capsys, build(), columns=columns, rows=ROWS)

        assert tallest <= ROWS, f"{name} at {columns}x{ROWS}: a frame filled {tallest} rows"


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
