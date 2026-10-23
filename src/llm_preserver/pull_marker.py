"""The pull claim's marker file: its name, its shape, and reading it safely.

Spec 0025's claim (``pull_claim``) decides *when* a marker is created,
judged, and released; this module owns *what* one is. Every read goes
through :func:`read_marker_bytes`, because anyone with write access to
a shared archive can put something at the marker's path: a symlink, a
FIFO that blocks a plain ``open`` forever, a device that never ends. A
marker that is not exactly this tool's shape is never treated as stale
and never deleted; the refusal names its path and leaves it for the
human.
"""

import errno
import json
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from llm_preserver.hub import PullEnvError, PullUserError, RepoFile
from llm_preserver.render import clean_text

PULL_MARKER_FILENAME = ".llm-preserver-pull.json"
MARKER_TOOL = "llm-preserver"
_FIELDS = frozenset({"tool", "repo_id", "hostname", "pid", "started"})
_MAX_MARKER_BYTES = 4096
# os.kill(0, ...) and os.kill(-1, ...) address process groups, and a pid
# past a C int raises OverflowError; anything outside this is malformed.
_MAX_PID = 2**31 - 1


class PullInProgressError(PullEnvError):
    """Another pull holds this repo's claim, or its marker cannot be judged."""


class NotRegularFileError(OSError):
    """The marker's path holds a FIFO, directory, or device, not a file."""


@dataclass(frozen=True)
class Holder:
    """A validated marker found in the way, with the bytes it was read from."""

    raw: bytes
    hostname: str
    pid: int
    started: str


def marker_bytes(repo_id: str, hostname: str, pid: int, started: str) -> bytes:
    """The exact bytes a claim writes, and later compares on release."""
    content = {
        "tool": MARKER_TOOL,
        "repo_id": repo_id,
        "hostname": hostname,
        "pid": pid,
        "started": started,
    }
    return json.dumps(content).encode("utf-8")


def read_marker_bytes(marker: Path) -> bytes | None:
    """Read at most one byte past the size cap, never following or blocking.

    Returns:
        The bytes read, or None when nothing is at ``marker``.

    Raises:
        NotRegularFileError: The path holds something other than a file.
        OSError: Unreadable; ``ELOOP`` when the path is a symlink.
    """
    try:
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFileError(errno.EINVAL, "not a regular file", str(marker))
        return os.read(fd, _MAX_MARKER_BYTES + 1)
    finally:
        os.close(fd)


def read_holder(marker: Path, repo_id: str) -> Holder | None:
    """The marker in the way, validated; None when it has just gone.

    Raises:
        PullInProgressError: Unreadable, a symlink, or not this shape —
            never treated as stale, never deleted.
    """
    try:
        raw = read_marker_bytes(marker)
    except NotRegularFileError as exc:
        raise _malformed(marker, repo_id, "not a regular file") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # O_NOFOLLOW met a symlink
            raise _malformed(marker, repo_id, "a symlink") from exc
        raise _unreadable(marker, repo_id, exc.strerror or str(exc)) from exc
    if raw is None:
        return None
    if len(raw) > _MAX_MARKER_BYTES:
        raise _malformed(marker, repo_id, "implausibly large")
    try:
        loaded = json.loads(raw)
    except (ValueError, RecursionError) as exc:  # RecursionError: older json decoders
        raise _malformed(marker, repo_id, "not JSON") from exc
    holder = _parse_holder(raw, loaded)
    if holder is None:
        raise _malformed(marker, repo_id, "not a marker this tool wrote")
    return holder


def require_no_marker_collision(selected: Sequence[RepoFile]) -> None:
    """Refuse a selection that would land on, or under, the marker's path.

    Staging keeps hub paths verbatim, so a root file of the marker's name
    downloads over it, and a root *directory* of that name cannot be
    created while the marker holds the path. Compared casefolded: the
    share may be case-insensitive. Deeper path components cannot collide.

    Raises:
        PullUserError: A selected path starts with the marker's name.
    """
    for repo_file in selected:
        first = repo_file.path.split("/", 1)[0]
        if first.casefold() == PULL_MARKER_FILENAME.casefold():
            raise PullUserError(
                f"the hub file {clean_text(repo_file.path, single_line=True)!r} would land on "
                "this tool's pull marker in staging; exclude it from the selection"
            )


def _parse_holder(raw: bytes, loaded: object) -> Holder | None:
    """The marker's fields when ``loaded`` is exactly this tool's shape."""
    if not isinstance(loaded, dict) or set(loaded) != _FIELDS or loaded["tool"] != MARKER_TOOL:
        return None
    pid, hostname, started = loaded["pid"], loaded["hostname"], loaded["started"]
    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= _MAX_PID:
        return None
    texts = (loaded["repo_id"], hostname, started)
    if not all(isinstance(text, str) and text for text in texts):
        return None
    try:
        datetime.fromisoformat(started)
    except ValueError:
        return None
    return Holder(raw, hostname, pid, started)


def _malformed(marker: Path, repo_id: str, reason: str) -> PullInProgressError:
    return PullInProgressError(
        f"the pull marker at {marker} is {reason}, so this tool cannot tell whether "
        f"another pull of {repo_id} is running; if none is, delete that file and re-run"
    )


def _unreadable(marker: Path, repo_id: str, reason: str) -> PullInProgressError:
    return PullInProgressError(
        f"cannot read the pull marker at {marker} ({reason}), so this tool cannot tell "
        f"whether another pull of {repo_id} is running; if none is, delete that file and re-run"
    )
