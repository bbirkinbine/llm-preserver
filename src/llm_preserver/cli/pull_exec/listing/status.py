"""Render archive observations without interpreting or rechecking evidence.

The snapshot owns evidence classification; these helpers only turn it into
file markers, group coverage, and the shared frame header and legend.
"""

from collections import Counter
from collections.abc import Sequence

from llm_preserver.archive_status import ArchiveStatusSnapshot, FileArchiveStatus
from llm_preserver.render import clean_text

_LABELS = {
    "archived": "archived",
    "not-archived": "not archived",
    "missing": "missing locally",
    "local-mismatch": "local size mismatch",
    "upstream-changed": "changed upstream",
    "unavailable": "status unavailable",
}
_EXCEPTIONS = ("missing", "local-mismatch", "upstream-changed", "unavailable")


def _file_status(path: str, status: ArchiveStatusSnapshot) -> FileArchiveStatus:
    if status.unavailable_reason is not None:
        return FileArchiveStatus("unavailable")
    return status.files.get(path, FileArchiveStatus("unavailable"))


def file_marker(path: str, status: ArchiveStatusSnapshot | None) -> str:
    """Render one file's observation, preserving alternate-candidate problems.

    Args:
        path: Original Hub path used as the snapshot key.
        status: The selected archive's observation, or None for legacy rendering.

    Returns:
        Bracketed status with leading spacing, or an empty string without status.
    """
    if status is None:
        return ""
    item = _file_status(path, status)
    label = _LABELS[item.state]
    if item.state == "archived" and item.comparison == "size":
        label += ": size only"
    markers = [label]
    markers.extend(
        _LABELS[state] for state in _EXCEPTIONS if state in item.issues and state != item.state
    )
    return "  " + " ".join(f"[{marker}]" for marker in markers)


def group_marker(paths: Sequence[str], status: ArchiveStatusSnapshot | None) -> str:
    """Count archived members once and expose every observed exception.

    Args:
        paths: Actual Hub paths represented by this row, including nested files.
        status: The selected archive's observation, or None for legacy rendering.

    Returns:
        Coverage and exception markers with leading spacing. A globally
        unreadable record produces only an unavailable marker, never zero coverage.
    """
    if status is None:
        return ""
    if status.unavailable_reason is not None or not paths:
        return "  [status unavailable]"
    items = [_file_status(path, status) for path in paths]
    archived = sum(item.state == "archived" for item in items)
    markers = [f"archived {archived}/{len(paths)}"]
    weaker = sum(item.state == "archived" and item.comparison == "size" for item in items)
    if weaker:
        markers.append(f"size only {weaker}")
    counts: Counter[str] = Counter()
    for item in items:
        counts.update(set(item.issues) | {item.state})
    markers.extend(f"{_LABELS[state]} {counts[state]}" for state in _EXCEPTIONS if counts[state])
    return "  " + " ".join(f"[{marker}]" for marker in markers)


def status_lines(status: ArchiveStatusSnapshot | None) -> list[str]:
    """Describe the archive and observation limits on each listing frame.

    Args:
        status: The selected archive's observation, or None for legacy rendering.

    Returns:
        Sanitized chrome lines; callers must charge all of them to frame budgets.
    """
    if status is None:
        return []
    lines = [
        f"archive: {status.archive}",
        "status: archive records + local metadata; no fresh checksum verification",
        "size only = weaker comparison; archived copies may use another layout",
    ]
    if status.unavailable_reason is not None:
        lines.append(f"status unavailable: {status.unavailable_reason}")
    return [clean_text(line, single_line=True) for line in lines]
