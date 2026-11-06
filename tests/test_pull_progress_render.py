"""Tests for the pull progress renderer (spec 0026, pass 2).

``render_progress_line`` and ``render_summary`` are pure: snapshot in,
one plain-text line out. Criterion 1 fixes the line shape; criterion 2
the floor wording (``of at least``, no percentage, no estimate);
criterion 3 the summary's two byte counts plus the reused and adopted
clauses. The rate is per window (a stall reads ``0 B/s``) while the
estimate uses the average since start (plan checkpoint). Numbers are
powers of two so every division is exact in floating point.
"""

from llm_preserver.pull_progress import (
    ProgressSnapshot,
    render_progress_line,
    render_summary,
)
from llm_preserver.render import clean_text

MIB = 1024**2
GIB = 1024**3


def _snap(**overrides: object) -> ProgressSnapshot:
    """A mid-pull snapshot: 32 GiB of 128 GiB after 256 s (128 MiB/s)."""
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


def _eta_snap(remaining_seconds: int) -> ProgressSnapshot:
    """1 MiB/s average over 64 s, with ``remaining_seconds`` of bytes left."""
    done = 64 * MIB
    return _snap(
        done_bytes=done,
        network_bytes=done,
        total=done + remaining_seconds * MIB,
        started_at=0.0,
        now=64.0,
    )


# --- line shape -------------------------------------------------------------


def test_known_total_line_has_bytes_percent_rate_estimate_and_file() -> None:
    assert render_progress_line(_snap(), None) == (
        "progress: 32.0 GiB of 128.0 GiB (25%), 128.0 MiB/s, ~12m48s left, "
        "downloading 3 of 12: m.gguf"
    )


def test_floor_total_says_at_least_and_omits_percent_and_estimate() -> None:
    line = render_progress_line(_snap(total_is_floor=True), None)
    assert line == (
        "progress: 32.0 GiB of at least 128.0 GiB, 128.0 MiB/s, downloading 3 of 12: m.gguf"
    )
    assert "%" not in line
    assert "left" not in line


def test_hashing_phase_names_the_phase_and_the_hash_percentage() -> None:
    snap = _snap(phase="hashing", hash_done=41, hash_total=100)
    assert render_progress_line(snap, None) == (
        "progress: 32.0 GiB of 128.0 GiB (25%), 128.0 MiB/s, ~12m48s left, "
        "hashing 3 of 12 (41%): m.gguf"
    )


def test_hashing_without_a_hash_total_omits_the_hash_percentage() -> None:
    for hash_total in (None, 0):
        snap = _snap(phase="hashing", hash_done=0, hash_total=hash_total)
        line = render_progress_line(snap, None)
        assert line.endswith(", hashing 3 of 12: m.gguf")


def test_percentage_is_floored() -> None:
    snap = _snap(total=1000, done_bytes=999, network_bytes=999)
    assert "(99%)" in render_progress_line(snap, None)


def test_percentage_caps_at_100_and_estimate_is_dropped_when_done() -> None:
    snap = _snap(total=100 * GIB, done_bytes=120 * GIB, network_bytes=120 * GIB)
    line = render_progress_line(snap, None)
    assert "(100%)" in line
    assert "left" not in line


# --- rate: per window, stall reads 0 B/s ------------------------------------


def test_rate_is_measured_over_the_window_since_the_previous_snapshot() -> None:
    prev = _snap(done_bytes=31 * GIB, network_bytes=31 * GIB, now=240.0)
    line = render_progress_line(_snap(), prev)
    # Window: 1 GiB in 16 s = 64 MiB/s; the estimate still uses the
    # 128 MiB/s average since start (96 GiB left -> 768 s).
    assert ", 64.0 MiB/s, " in line
    assert "~12m48s left" in line


def test_stall_reads_zero_bytes_per_second() -> None:
    prev = _snap(now=226.0)
    line = render_progress_line(_snap(), prev)
    assert ", 0 B/s, " in line


def test_no_estimate_when_the_average_rate_is_zero() -> None:
    snap = _snap(done_bytes=0, network_bytes=0)
    line = render_progress_line(snap, None)
    assert line.startswith("progress: 0 B of 128.0 GiB (0%), 0 B/s, ")
    assert "left" not in line


def test_zero_length_window_does_not_raise() -> None:
    at_start = _snap(now=0.0)
    assert render_progress_line(at_start, None).startswith("progress: ")
    assert render_progress_line(_snap(), _snap()).startswith("progress: ")


# --- estimate duration format -----------------------------------------------


def test_estimate_under_a_minute_is_seconds_only() -> None:
    assert "~45s left" in render_progress_line(_eta_snap(45), None)


def test_estimate_with_minutes_zero_pads_seconds() -> None:
    assert "~6m40s left" in render_progress_line(_eta_snap(400), None)
    assert "~1m01s left" in render_progress_line(_eta_snap(61), None)


def test_estimate_with_hours_zero_pads_minutes_and_drops_seconds() -> None:
    assert "~1h05m left" in render_progress_line(_eta_snap(3900), None)
    assert "~1h05m left" in render_progress_line(_eta_snap(3959), None)


# --- output hygiene ---------------------------------------------------------


def test_hostile_path_stays_on_one_line_with_no_escape_sequences() -> None:
    hostile = "evil\n\x1b[31mINFO llm_preserver: forged\r.gguf"
    line = render_progress_line(_snap(path=hostile), None)
    assert "\n" not in line
    assert "\r" not in line
    assert "\x1b" not in line
    assert line.endswith(": " + clean_text(hostile, single_line=True))


def test_no_carriage_return_or_escape_in_any_output() -> None:
    snaps = [
        _snap(),
        _snap(total_is_floor=True),
        _snap(phase="hashing", hash_done=5, hash_total=10),
        _snap(reused_files=2, reused_bytes=GIB, adopted_bytes=GIB),
    ]
    outputs = [render_progress_line(s, None) for s in snaps]
    outputs += [render_summary(s) for s in snaps]
    for out in outputs:
        assert "\r" not in out
        assert "\x1b" not in out
        assert "\n" not in out


# --- summary ----------------------------------------------------------------


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


def test_summary_carries_both_byte_counts_elapsed_and_average() -> None:
    assert render_summary(_done_snap()) == (
        "transferred 64.0 GiB (archived 65.0 GiB) in 8m32s, 128.0 MiB/s average"
    )


def test_summary_adds_the_reused_clause_only_when_files_were_reused() -> None:
    summary = render_summary(_done_snap(reused_files=2, reused_bytes=3 * GIB))
    assert summary == (
        "transferred 64.0 GiB (archived 65.0 GiB) in 8m32s, 128.0 MiB/s average"
        "; reused 2 files (3.0 GiB) from staging"
    )
    assert "reused" not in render_summary(_done_snap())


def test_summary_adds_the_adopted_clause_only_when_bytes_were_adopted() -> None:
    summary = render_summary(_done_snap(adopted_bytes=GIB))
    assert summary == (
        "transferred 64.0 GiB (archived 65.0 GiB) in 8m32s, 128.0 MiB/s average"
        "; adopted 1.0 GiB already on disk"
    )
    assert "adopted" not in render_summary(_done_snap())


def test_summary_orders_reused_before_adopted() -> None:
    summary = render_summary(_done_snap(reused_files=2, reused_bytes=3 * GIB, adopted_bytes=GIB))
    assert summary.endswith(
        "; reused 2 files (3.0 GiB) from staging; adopted 1.0 GiB already on disk"
    )


def test_summary_elapsed_uses_hours_and_minutes_for_long_pulls() -> None:
    summary = render_summary(_done_snap(started_at=0.0, now=3900.0))
    assert " in 1h05m, " in summary


def test_summary_with_zero_elapsed_does_not_divide_by_zero() -> None:
    snap = _done_snap(network_bytes=0, done_bytes=0, archived_bytes=0, started_at=5.0, now=5.0)
    assert render_summary(snap) == "transferred 0 B (archived 0 B) in 0s, 0 B/s average"
