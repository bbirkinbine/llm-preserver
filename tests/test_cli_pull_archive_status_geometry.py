"""Annotated frame chrome and row wrapping are charged at every width."""

from pathlib import Path
from typing import Any  # Shared prompt harness accepts pytest capture and scripted callbacks.

import click
import pytest
import typer
from test_cli_pull_listing_tty import (
    FOOTER_RE,
    ROLLUP_KEYS,
    Prompter,
    info_for,
    kimi_repo,
    paths_in,
    physical_height,
    scripted,
    walk_all,
)

import llm_preserver.cli.pull_exec.prompts as prompts
from llm_preserver.archive_status import ArchiveStatusSnapshot, FileArchiveStatus
from llm_preserver.cli.pull_exec.listing import FLAT_KEYS
from llm_preserver.cli.window import MIN_WINDOW_ROWS
from llm_preserver.hub import RepoInfo
from llm_preserver.pull_decline import PullDeclined
from llm_preserver.text_window import wrapped_height

# The one-screen sweep the repo standard asks for (spec 0018's lesson: a
# frame-sizing test pinned to one geometry cannot fail).
SWEEP_COLUMNS = range(30, 121, 7)
SWEEP_ROWS = range(14, 61, 4)

# A mix of observations, so group rows carry several exception markers
# and wrap the way a real partially archived repo does.
MIXED = (
    FileArchiveStatus("archived", "size"),
    FileArchiveStatus("archived", "sha256"),
    FileArchiveStatus("not-archived"),
    FileArchiveStatus("missing", issues=("local-mismatch",)),
    FileArchiveStatus("upstream-changed"),
)


def all_archived(info: RepoInfo) -> ArchiveStatusSnapshot:
    """Every file archived by size, the representative snapshot."""
    return ArchiveStatusSnapshot(
        Path("/archive/model-library"),
        {file.path: FileArchiveStatus("archived", "size") for file in info.files},
    )


def mixed_status(info: RepoInfo) -> ArchiveStatusSnapshot:
    """Cycle through the observation kinds so markers are long."""
    return ArchiveStatusSnapshot(
        Path("/archive/model-library"),
        {file.path: MIXED[index % len(MIXED)] for index, file in enumerate(info.files)},
    )


def annotated_walk(
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
    info: RepoInfo,
    answer: Any,
    *,
    columns: int = 80,
    rows: int = 32,
    status: ArchiveStatusSnapshot | None = None,
    clamps: list[int] | None = None,
) -> tuple[list[str], Prompter]:
    """Use the existing capture harness and a representative status snapshot.

    ``clamps`` collects every chrome the budget floor overrode: there the
    ``MIN_WINDOW_ROWS`` floor wins over the screen by design, so a sweep
    must not charge those geometries as overflow.
    """
    snapshot = status if status is not None else all_archived(info)
    seen = clamps if clamps is not None else []

    def budget(stream: Any, chrome: int) -> int:
        if rows - chrome < MIN_WINDOW_ROWS:
            seen.append(chrome)
        return max(MIN_WINDOW_ROWS, rows - chrome)

    prompter = Prompter(capsys, answer, limit=300)
    monkeypatch.setattr(typer, "prompt", prompter)
    monkeypatch.setattr(prompts, "is_interactive", lambda stream: True)
    monkeypatch.setattr(prompts, "resolve_window_width", lambda stream: columns)
    monkeypatch.setattr(prompts, "resolve_window_size", budget)
    return prompts.prompt_for_selection(info, "acme/tiny-chat", status=snapshot), prompter


def overflows(capture: Prompter, columns: int, rows: int) -> list[int]:
    """Physical heights, prompt included, of every frame taller than the screen."""
    heights = [
        physical_height(frame, columns) + wrapped_height(f"{prompt}: ", columns)
        for frame, prompt in zip(capture.frames, capture.texts, strict=True)
    ]
    return [height for height in heights if height > rows]


def quant_files() -> RepoInfo:
    """Two collapsing groups, with long rows whose markers wrap."""
    return info_for(
        [
            (f"Q{quant}/tiny-chat-long-shard-name-{index:05d}.gguf", 3)
            for quant in (4, 8)
            for index in range(35)
        ]
    )


@pytest.mark.parametrize("columns", [30, 38, 42, 48, 60, 76, 77, 80, 100, 120])
@pytest.mark.parametrize("rows", [36, 50])
def test_annotated_frames_fit_and_reach_all_files(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, columns: int, rows: int
) -> None:
    info = quant_files()
    patterns, capture = annotated_walk(
        monkeypatch, capsys, info, walk_all(), columns=columns, rows=rows
    )
    assert patterns == ["*.gguf"]
    windows = [frame for frame in capture.frames if FOOTER_RE.search(frame)]
    assert len(windows) > 1
    assert [path for frame in windows for path in paths_in(frame, info)] == [
        file.path for file in info.files
    ]
    assert all(
        "size only" in frame and "/archive/model-library" in frame for frame in capture.frames
    )
    for frame, prompt in zip(capture.frames, capture.texts, strict=True):
        height = physical_height(frame, columns) + wrapped_height(f"{prompt}: ", columns)
        assert height <= rows, f"{columns}x{rows}: frame uses {height} rows\n{frame}"


def test_tiny_annotated_terminal_still_advances_to_every_file(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    info = quant_files()
    patterns, capture = annotated_walk(monkeypatch, capsys, info, walk_all(), columns=30, rows=8)
    assert patterns == ["*.gguf"]
    windows = [frame for frame in capture.frames if FOOTER_RE.search(frame)]
    assert len(windows) > 1
    assert all(paths_in(frame, info) for frame in windows)
    assert [path for frame in windows for path in paths_in(frame, info)] == [
        file.path for file in info.files
    ]


def test_summary_toggle_and_back_preserve_annotated_page_position(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    info = quant_files()
    patterns, capture = annotated_walk(
        monkeypatch, capsys, info, scripted("f", "m", "b", "m", "s", "f", "Q4/*")
    )
    assert patterns == ["Q4/*"]
    frames = capture.frames
    assert "[archived 35/35]" in frames[0]
    assert frames[1] == frames[3]
    assert frames[2] == frames[4] == frames[6]
    assert frames[0] == frames[5]


def test_quit_still_declines_without_turning_into_a_pattern(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    with pytest.raises(PullDeclined):
        annotated_walk(monkeypatch, capsys, quant_files(), scripted("q"))


def test_unavailable_snapshot_still_accepts_patterns(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    info = info_for([("a.gguf", 3)])
    state = ArchiveStatusSnapshot(
        Path("/archive"),
        {"a.gguf": FileArchiveStatus("unavailable")},
        "cannot read model record",
    )
    monkeypatch.setattr(prompts, "is_interactive", lambda stream: False)
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "a.gguf")
    assert prompts.prompt_for_selection(info, "acme/tiny-chat", status=state) == ["a.gguf"]
    output = click.unstyle(capsys.readouterr().out)
    assert output.count("cannot read model record") == 1
    assert "[status unavailable]" in output


def boundary_repo() -> RepoInfo:
    """Eleven root files: flat when tall, paged when short, at every width."""
    names = ["README.md", "config.json", "generation_config.json", "mmproj-F16.gguf"]
    names += [f"tiny-chat-{quant}.gguf" for quant in ("Q2_K", "Q3_K_M", "Q4_K_M", "Q5_K_M")]
    names += [f"tiny-chat-{quant}.gguf" for quant in ("Q6_K", "Q8_0", "F16")]
    return info_for([(name, 4096) for name in names])


def sweep(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, info: RepoInfo
) -> tuple[list[str], list[str]]:
    """Walk every geometry; return the overflows and each checked opening frame."""
    failures: list[str] = []
    openings: list[str] = []
    for columns in SWEEP_COLUMNS:
        for rows in SWEEP_ROWS:
            clamps: list[int] = []
            _, capture = annotated_walk(
                monkeypatch,
                capsys,
                info,
                walk_all(),
                columns=columns,
                rows=rows,
                status=mixed_status(info),
                clamps=clamps,
            )
            if clamps:
                continue
            openings.append(capture.frames[0])
            if tall := overflows(capture, columns, rows):
                failures.append(f"{columns}x{rows}: {tall}")
    return failures, openings


def test_annotated_rollup_is_charged_its_context_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The roll-up offer counts the archive header and legend at every size.

    Kimi-shaped: eight shard directories, so the roll-up sits near the
    budget boundary across the sweep and is offered at some heights and
    withheld at others. Removing ``*context`` from ``rollup_budget``
    offered roll-ups that ran up to six rows past the screen.
    """
    failures, openings = sweep(monkeypatch, capsys, kimi_repo())
    assert not failures, "annotated frames overflow:\n" + "\n".join(failures)
    rollups = sum(ROLLUP_KEYS in frame for frame in openings)
    assert rollups >= 20, f"sweep reached the roll-up only {rollups} times"
    assert len(openings) - rollups >= 20, "sweep never withheld the roll-up"


def test_annotated_flat_fit_is_charged_its_context_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The flat-fits check counts the archive header and legend at every size.

    An eleven-file repo fits flat at tall sizes and pages at short ones, so the
    sweep crosses the fits boundary at every width. Removing ``*context``
    from the fits check printed flat frames past the screen.
    """
    failures, openings = sweep(monkeypatch, capsys, boundary_repo())
    assert not failures, "annotated frames overflow:\n" + "\n".join(failures)
    flats = sum(
        frame.rstrip().endswith(FLAT_KEYS) and not FOOTER_RE.search(frame) for frame in openings
    )
    assert flats >= 20, f"sweep reached the flat listing only {flats} times"
    assert len(openings) - flats >= 20, "sweep never paged the repo"
