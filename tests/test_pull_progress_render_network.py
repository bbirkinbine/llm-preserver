"""Renderer review-round fixes for ``pull --progress`` (spec 0026, criterion 3).

Amended 2026-11-06: the rate in each line counts network bytes only, and
the time estimate is the remaining bytes (``total - done``) over the
average *network* rate since start. Credited files (reused from staging,
copied from the local HF cache) shorten what is left but never make the
link look faster. The first implementation divided ``done`` by elapsed
time and printed ``~59s left`` for a 50-minute remainder on a resumed
pull. Also here: the ``copying`` phase, the summary's copied clause, and
guards for behavior a mutation removed undetected. Numbers are powers of
two so every division is exact in floating point.
"""

import dataclasses
import typing

from llm_preserver.pull_progress import (
    Phase,
    ProgressSnapshot,
    render_progress_line,
    render_summary,
)
from llm_preserver.render import clean_text

MIB = 1024**2
GIB = 1024**3


def _snap(**overrides: object) -> ProgressSnapshot:
    """A mid-pull snapshot: 32 GiB of 128 GiB after 256 s, all over the network."""
    fields: dict[str, object] = {
        "total": 128 * GIB,
        "total_is_floor": False,
        "file_count": 12,
        "index": 3,
        "path": "m.gguf",
        "phase": "downloading",
        "network_bytes": 32 * GIB,
        "done_bytes": 32 * GIB,
        "archived_bytes": 0,
        "reused_files": 0,
        "reused_bytes": 0,
        "adopted_bytes": 0,
        "hash_done": 0,
        "hash_total": None,
        "started_at": 0.0,
        "now": 256.0,
    }
    fields.update(overrides)
    return ProgressSnapshot(**fields)  # type: ignore[arg-type]


def _done_snap(**overrides: object) -> ProgressSnapshot:
    """A finished pull: 64 GiB received, 65 GiB archived, in 512 s."""
    fields: dict[str, object] = {
        "network_bytes": 64 * GIB,
        "done_bytes": 64 * GIB,
        "archived_bytes": 65 * GIB,
        "started_at": 1000.0,
        "now": 1512.0,
    }
    fields.update(overrides)
    return _snap(**fields)


SUMMARY_HEAD = "transferred 64.0 GiB (archived 65.0 GiB) in 8m32s, 128.0 MiB/s average"


# --- 1. rate counts network bytes, never credited ones ----------------------


def test_first_line_rate_ignores_credited_bytes_and_a_stalled_resume_reads_zero() -> None:
    snap = _snap(done_bytes=50 * GIB, network_bytes=0, now=30.0)
    assert ", 0 B/s, " in render_progress_line(snap, None)


def test_first_line_rate_is_network_bytes_over_time_since_start() -> None:
    # 4 GiB received in 64 s = 64 MiB/s, though 64 GiB counts as done.
    snap = _snap(done_bytes=64 * GIB, network_bytes=4 * GIB, now=64.0)
    assert ", 64.0 MiB/s, " in render_progress_line(snap, None)


def test_window_rate_is_the_network_delta_even_when_a_reused_file_landed_in_it() -> None:
    # Window: 1 GiB received in 16 s = 64 MiB/s; a 32 GiB reused file also
    # finished inside it and must not show up as speed.
    prev = _snap(network_bytes=1 * GIB, done_bytes=1 * GIB, now=240.0)
    snap = _snap(network_bytes=2 * GIB, done_bytes=34 * GIB, now=256.0)
    assert ", 64.0 MiB/s, " in render_progress_line(snap, prev)


# --- 2. estimate: remaining bytes over the network average ------------------


def test_estimate_divides_what_is_left_by_the_network_average_since_start() -> None:
    # 64 GiB done (60 GiB credited), 4 GiB received in 64 s = 64 MiB/s;
    # 64 GiB left / 64 MiB/s = 1024 s. Dividing done by elapsed would say 64 s.
    snap = _snap(done_bytes=64 * GIB, network_bytes=4 * GIB, now=64.0)
    assert render_progress_line(snap, None) == (
        "progress: 64.0 GiB of 128.0 GiB (50%), 64.0 MiB/s, ~17m04s left, "
        "downloading 3 of 12: m.gguf"
    )


def test_estimate_uses_the_average_since_start_not_the_window() -> None:
    prev = _snap(network_bytes=4 * GIB - 16 * MIB, done_bytes=64 * GIB - 16 * MIB, now=48.0)
    snap = _snap(done_bytes=64 * GIB, network_bytes=4 * GIB, now=64.0)
    line = render_progress_line(snap, prev)
    assert ", 1.0 MiB/s, " in line
    assert "~17m04s left" in line


def test_credited_bytes_with_nothing_received_give_no_estimate() -> None:
    snap = _snap(total=100 * GIB, done_bytes=50 * GIB, network_bytes=0, now=30.0)
    assert "left" not in render_progress_line(snap, None)


# --- 3. mutation-survivor guards (pass on f1fa8e9; pin what a mutant removed)


def test_summary_says_file_singular_for_one_reused_file() -> None:
    summary = render_summary(_done_snap(reused_files=1, reused_bytes=GIB))
    assert summary == SUMMARY_HEAD + "; reused 1 file (1.0 GiB) from staging"


def test_negative_window_after_a_ranged_retry_rewind_reads_zero_not_negative() -> None:
    prev = _snap(network_bytes=2 * GIB, done_bytes=2 * GIB, now=240.0)
    snap = _snap(network_bytes=1 * GIB, done_bytes=1 * GIB, now=256.0)
    line = render_progress_line(snap, prev)
    assert ", 0 B/s, " in line
    assert "-" not in line


def test_hash_percentage_caps_at_100_when_more_was_read_than_expected() -> None:
    snap = _snap(phase="hashing", hash_done=150, hash_total=100)
    assert render_progress_line(snap, None).endswith(", hashing 3 of 12 (100%): m.gguf")


# --- 4. copying phase -------------------------------------------------------


def test_phase_literal_includes_copying() -> None:
    assert set(typing.get_args(Phase)) == {"downloading", "hashing", "copying"}


def test_copying_phase_names_the_local_hf_cache() -> None:
    snap = _snap(phase="copying")
    assert render_progress_line(snap, None).endswith(
        ", copying 3 of 12 from local HF cache: m.gguf"
    )


def test_copying_phase_path_is_scrubbed_to_one_line() -> None:
    hostile = "evil\n\x1b[31mINFO llm_preserver: forged\r.gguf"
    line = render_progress_line(_snap(phase="copying", path=hostile), None)
    assert "\n" not in line
    assert "\r" not in line
    assert "\x1b" not in line
    assert line.endswith(" from local HF cache: " + clean_text(hostile, single_line=True))


# --- 5. summary copied clause ----------------------------------------------


def test_snapshot_copied_fields_are_last_and_default_to_zero() -> None:
    fields = dataclasses.fields(ProgressSnapshot)
    assert [f.name for f in fields[-2:]] == ["copied_files", "copied_bytes"]
    assert [f.default for f in fields[-2:]] == [0, 0]


def test_summary_adds_the_copied_clause_when_files_came_from_the_cache() -> None:
    summary = render_summary(_done_snap(copied_files=2, copied_bytes=3 * GIB))
    assert summary == SUMMARY_HEAD + "; copied 2 files (3.0 GiB) from local HF cache"


def test_summary_copied_clause_says_file_singular_for_one_file() -> None:
    summary = render_summary(_done_snap(copied_files=1, copied_bytes=GIB))
    assert summary.endswith("; copied 1 file (1.0 GiB) from local HF cache")


def test_summary_omits_the_copied_clause_when_nothing_was_copied() -> None:
    assert "copied" not in render_summary(_done_snap(copied_files=0, copied_bytes=0))


def test_summary_orders_reused_then_copied_then_adopted() -> None:
    snap = _done_snap(
        reused_files=2, reused_bytes=3 * GIB, copied_files=1, copied_bytes=GIB, adopted_bytes=GIB
    )
    assert render_summary(snap) == (
        SUMMARY_HEAD + "; reused 2 files (3.0 GiB) from staging"
        "; copied 1 file (1.0 GiB) from local HF cache"
        "; adopted 1.0 GiB already on disk"
    )
