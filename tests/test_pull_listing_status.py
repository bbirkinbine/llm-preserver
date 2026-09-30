"""Status counts follow actual group members and preserve file-kind notes."""

from pathlib import Path

import pytest

from llm_preserver import archive_status
from llm_preserver.cli.pull_exec.listing import flat_lines, group_files, rollup_lines
from llm_preserver.cli.pull_exec.listing.status import status_lines
from llm_preserver.hub import RepoFile


def snapshot(states: dict[str, tuple[str, str | None]], *, unavailable: bool = False) -> object:
    """Build display evidence without filesystem or terminal coupling."""
    return archive_status.ArchiveStatusSnapshot(
        Path("/archive"),
        {
            path: archive_status.FileArchiveStatus(state, comparison)
            for path, (state, comparison) in states.items()
        },
        "record unreadable" if unavailable else None,
    )


def test_nested_directory_counts_match_expanded_rows_and_preserve_order() -> None:
    files = [
        RepoFile(path, 3, None)
        for path in ["Q8/a.gguf", "Q4/nested/a.gguf", "Q4/b.gguf", "Q8/b.gguf", "Q2/a.gguf"]
    ]
    status = snapshot(
        {
            f.path: ("archived" if index in (0, 1, 3) else "not-archived", "sha256")
            for index, f in enumerate(files)
        }
    )
    rows = rollup_lines(group_files(files), status=status)
    assert "Q8/" in rows[0] and "[archived 2/2]" in rows[0]
    assert "Q4/" in rows[1] and "[archived 1/2]" in rows[1]
    assert "Q2/" in rows[2] and "[archived 0/1]" in rows[2]
    flat = flat_lines(files, status=status)
    assert sum("[archived]" in line for line in flat) == 3
    for file, row in zip(files, flat, strict=True):
        assert file.path in row


def test_root_shard_set_uses_member_statuses() -> None:
    files = [RepoFile(f"model-{index:05d}-of-00003.safetensors", 3, None) for index in range(1, 4)]
    status = snapshot(
        {
            file.path: ("archived" if index < 2 else "missing", "sha256")
            for index, file in enumerate(files)
        }
    )
    rows = rollup_lines(group_files(files), status=status)
    assert len(rows) == 1
    assert "[archived 2/3]" in rows[0]
    assert "missing locally" in rows[0]


@pytest.mark.parametrize(
    ("state", "comparison", "marker"),
    [
        ("archived", "sha256", "[archived]"),
        ("archived", "size", "[archived: size only]"),
        ("missing", None, "[missing locally]"),
        ("local-mismatch", None, "[local size mismatch]"),
        ("upstream-changed", None, "[changed upstream]"),
        ("unavailable", None, "[status unavailable]"),
        ("not-archived", None, "[not archived]"),
    ],
)
def test_each_file_status_is_explicit_and_preserves_companion_note(
    state: str, comparison: str | None, marker: str
) -> None:
    files = [RepoFile("mmproj-F16.gguf", 3, None)]
    status = snapshot({files[0].path: (state, comparison)})
    for row in [
        flat_lines(files, status=status)[0],
        rollup_lines(group_files(files), status=status)[0],
    ]:
        assert marker in row
        assert "vision projector" in row


def test_group_discloses_weaker_comparisons_and_every_exception_kind() -> None:
    states = {
        f"Q4/{state}.gguf": (state, "size" if state == "archived" else None)
        for state in ("archived", "missing", "local-mismatch", "upstream-changed", "unavailable")
    }
    files = [RepoFile(path, 3, None) for path in states]
    row = rollup_lines(group_files(files), status=snapshot(states))[0]
    assert "[archived 1/5]" in row
    for marker in (
        "size only",
        "missing locally",
        "local size mismatch",
        "changed upstream",
        "status unavailable",
    ):
        assert marker in row


def test_unreadable_record_never_renders_zero_archived_count() -> None:
    files = [RepoFile("Q4/a.gguf", 3, None), RepoFile("Q4/b.gguf", 3, None)]
    status = snapshot({file.path: ("unavailable", None) for file in files}, unavailable=True)
    row = rollup_lines(group_files(files), status=status)[0]
    assert "status unavailable" in row
    assert "archived 0/" not in row


def test_problems_with_other_recorded_copies_stay_visible_on_rows_and_groups() -> None:
    files = [RepoFile("Q4/a.gguf", 3, None), RepoFile("Q4/b.gguf", 3, None)]
    status = archive_status.ArchiveStatusSnapshot(
        Path("/archive"),
        {
            "Q4/a.gguf": archive_status.FileArchiveStatus("archived", "sha256", ("missing",)),
            "Q4/b.gguf": archive_status.FileArchiveStatus("archived", "sha256"),
        },
    )
    row = next(line for line in flat_lines(files, status=status) if "Q4/a.gguf" in line)
    assert "[archived] [missing locally]" in row
    group = rollup_lines(group_files(files), status=status)[0]
    assert "[archived 2/2]" in group
    assert "[missing locally 1]" in group


def test_archive_path_in_frame_header_is_scrubbed() -> None:
    status = archive_status.ArchiveStatusSnapshot(Path("/arch\x1b[2Jive\nforged"), {})
    lines = status_lines(status)
    assert all("\x1b" not in line and "\n" not in line for line in lines)
