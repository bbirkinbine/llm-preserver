"""Plain-text progress and summary lines for ``pull --progress``.

Pure functions, snapshot in and one line out (spec 0026). Every line is
self-contained and carries no carriage return or terminal control
sequence, so it reads the same in a log file, a pipe, or an agent
transcript. The hub path is untrusted and goes through ``clean_text``.
"""

from llm_preserver.pull_preflight import human_size
from llm_preserver.pull_progress.meter import ProgressSnapshot
from llm_preserver.render import clean_text


def _duration(seconds: float) -> str:
    """Render seconds as ``45s``, ``6m40s`` or ``1h05m``."""
    total = max(int(seconds + 0.5), 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _rate(byte_count: int, seconds: float) -> float:
    """Bytes per second, 0 when the window has no length."""
    return byte_count / seconds if seconds > 0 else 0.0


def _per_second(rate: float) -> str:
    return f"{human_size(int(rate))}/s"


def _amount(snap: ProgressSnapshot) -> str:
    """The ``done of total`` clause, with percentage when exact."""
    done = human_size(snap.done_bytes)
    if snap.total_is_floor:
        return f"{done} of at least {human_size(snap.total)}"
    percent = min(snap.done_bytes * 100 // snap.total, 100) if snap.total > 0 else 100
    return f"{done} of {human_size(snap.total)} ({percent}%)"


def _estimate(snap: ProgressSnapshot) -> str | None:
    """The ``~6m40s left`` clause: what is left over the network average.

    What is left counts credited reused and copied files as done; the
    speed counts only bytes that crossed the network, so a resumed pull
    never looks faster than its link (spec 0026 criterion 3). None when
    the total is a floor, nothing has been received yet, or the pull has
    reached its total: no guess is printed as if it were exact.
    """
    if snap.total_is_floor or snap.done_bytes >= snap.total:
        return None
    average = _rate(snap.network_bytes, snap.now - snap.started_at)
    if average <= 0:
        return None
    return f"~{_duration((snap.total - snap.done_bytes) / average)} left"


def _file_clause(snap: ProgressSnapshot) -> str:
    """``downloading 3 of 12: <path>``, ``hashing 3 of 12 (41%): <path>``,
    or ``copying 3 of 12 from local HF cache: <path>``."""
    position = f"{snap.phase} {snap.index} of {snap.file_count}"
    if snap.phase == "copying":
        position = f"{position} from local HF cache"
    if snap.phase == "hashing" and snap.hash_total:
        percent = min(snap.hash_done * 100 // snap.hash_total, 100)
        position = f"{position} ({percent}%)"
    return f"{position}: {clean_text(snap.path, single_line=True)}"


def render_progress_line(snap: ProgressSnapshot, prev: ProgressSnapshot | None) -> str:
    """Render one periodic progress line.

    Args:
        snap: The current moment.
        prev: The moment the previous line was printed, or None for the
            first line; the rate covers the window between them and
            counts network bytes only, so a stall reads ``0 B/s`` even
            when a reused file was credited inside the window.

    Returns:
        The line, without a trailing newline.
    """
    if prev is None:
        window = _rate(snap.network_bytes, snap.now - snap.started_at)
    else:
        window = _rate(snap.network_bytes - prev.network_bytes, snap.now - prev.now)
    clauses = [_amount(snap), _per_second(max(window, 0.0))]
    estimate = _estimate(snap)
    if estimate is not None:
        clauses.append(estimate)
    clauses.append(_file_clause(snap))
    return "progress: " + ", ".join(clauses)


def render_summary(snap: ProgressSnapshot) -> str:
    """Render the closing summary line of a completed pull.

    Args:
        snap: The final moment of the pull.

    Returns:
        Bytes received and archived, elapsed time, average rate, and
        the reused, copied and adopted clauses when they apply.
    """
    elapsed = snap.now - snap.started_at
    line = (
        f"transferred {human_size(snap.network_bytes)} "
        f"(archived {human_size(snap.archived_bytes)}) in {_duration(elapsed)}, "
        f"{_per_second(_rate(snap.network_bytes, elapsed))} average"
    )
    if snap.reused_files > 0:
        noun = "file" if snap.reused_files == 1 else "files"
        line += (
            f"; reused {snap.reused_files} {noun} ({human_size(snap.reused_bytes)}) from staging"
        )
    if snap.copied_files > 0:
        noun = "file" if snap.copied_files == 1 else "files"
        line += (
            f"; copied {snap.copied_files} {noun} ({human_size(snap.copied_bytes)})"
            " from local HF cache"
        )
    if snap.adopted_bytes > 0:
        line += f"; adopted {human_size(snap.adopted_bytes)} already on disk"
    return line
