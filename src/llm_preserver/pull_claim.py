"""Claiming a repo for the duration of a pull (spec 0025).

Two pulls of the same repo share one staging leaf and one record: the
first to finish deletes the leaf under the second, and the later
``save_record`` overwrites the earlier from a stale copy. A pull
therefore claims its repo with a marker file in its staging leaf,
created by an exclusive create, and a second pull of the same repo is
refused while the claim is held. Pulls of different repos never meet a
marker, so they stay fully parallel.

The archive can sit on a share mounted by several machines, so nothing
here trusts a ``stat``: an attribute cache can answer it, while an
``O_EXCL`` create is decided by the server. A marker left by a hard kill
is cleared only when the tool can prove its holder is gone — same host,
pid not running. Anything else is reported with its path and left for
the human; this module never offers a flag that deletes it.
"""

import contextlib
import logging
import os
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from llm_preserver.hub import PullEnvError
from llm_preserver.layout import staging_dir_for
from llm_preserver.pull_marker import (
    PULL_MARKER_FILENAME,
    Holder,
    PullInProgressError,
    marker_bytes,
    read_holder,
    read_marker_bytes,
    require_no_marker_collision,
)
from llm_preserver.render import clean_text
from llm_preserver.staging_cleanup import discard_empty_staging_leaf

logger = logging.getLogger(__name__)

__all__ = [
    "PULL_MARKER_FILENAME",
    "PullInProgressError",
    "claim_pull",
    "claim_repo",
    "require_no_marker_collision",
]

# Bounds the retries for a sibling's owner-directory rmdir landing
# between our mkdir and our create, and for one stale takeover.
_CLAIM_ATTEMPTS = 3


def _this_host() -> str:
    """The hostname a marker is compared against (a seam for tests)."""
    return socket.gethostname()


def claim_repo(archive_root: Path, repo_id: str) -> contextlib.AbstractContextManager[None]:
    """The claim for a pull of ``repo_id``, held around planning and transfer.

    An id that cannot name a staging leaf (an Ollama ``name:tag``) takes
    no claim: it cannot reach a write, and the hub's validator gives it
    the 0011/0013 error and hint unchanged.
    """
    try:
        leaf = staging_dir_for(archive_root, repo_id)
    except ValueError:
        return contextlib.nullcontext()
    return claim_pull(leaf, repo_id)


@contextlib.contextmanager
def claim_pull(leaf: Path, repo_id: str) -> Iterator[None]:
    """Hold ``repo_id``'s claim in ``leaf`` for the body of the ``with``.

    Release runs on every exit, Ctrl-C included, and removes only a
    marker whose bytes are still this run's: a marker another process
    wrote over this one (a human's ``rm`` and a fresh claim, or a
    hand-edited file) is not this run's to delete.

    Raises:
        PullInProgressError: Another pull holds the claim, or the marker
            in the way is malformed or unreadable.
        PullEnvError: The marker could not be created.
    """
    marker = leaf / PULL_MARKER_FILENAME
    started = datetime.now(UTC).isoformat(timespec="seconds")
    own = marker_bytes(repo_id, _this_host(), os.getpid(), started)
    _acquire(marker, own, repo_id)
    try:
        yield
    finally:
        _release(marker, own)


def _acquire(marker: Path, own: bytes, repo_id: str) -> None:
    for _ in range(_CLAIM_ATTEMPTS):
        try:
            _create_exclusive(marker, own)
            return
        except FileExistsError:
            pass
        except FileNotFoundError:
            continue  # a sibling's owner-directory rmdir won the race
        except OSError as exc:
            raise PullEnvError(
                f"cannot create the pull marker at {marker} ({exc.strerror}); "
                "check permissions on the archive's .staging directory"
            ) from exc
        holder = read_holder(marker, repo_id)
        if holder is None:
            continue  # the holder released between our create and our read
        _refuse_unless_stale(marker, holder, repo_id)
        if _unlink_if_unchanged(marker, holder, repo_id):
            logger.info(
                "cleared a stale pull marker for %s left by pid %d on this host "
                "(no longer running)",
                repo_id,
                holder.pid,
            )
    raise PullInProgressError(
        f"could not claim {repo_id} at {marker} after {_CLAIM_ATTEMPTS} attempts: the "
        "marker or its directory kept changing underneath this run; re-run, and if "
        f"this repeats, check what else is writing to {marker.parent}"
    )


def _create_exclusive(marker: Path, own: bytes) -> None:
    """Create the marker or fail; the create call is the whole test."""
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, own)
        os.fsync(fd)
    # BaseException: a Ctrl-C or Terminated during the write (an SMB
    # round trip) must not leave a half-written marker behind.
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            marker.unlink()
        raise
    os.close(fd)


def _unlink_if_unchanged(marker: Path, holder: Holder, repo_id: str) -> bool:
    """Remove a stale marker only if it is still the one judged stale.

    Raises:
        PullInProgressError: The stale marker could not be re-read or
            deleted (another user's leaf, a read-only share).
    """
    try:
        if read_marker_bytes(marker) != holder.raw:
            return False
        marker.unlink()
    except FileNotFoundError:
        return False  # its holder's release or a human got there first
    except OSError as exc:
        raise PullInProgressError(
            f"a stale pull marker for {repo_id} (left by pid {holder.pid} on this "
            f"host, no longer running) could not be deleted ({exc.strerror}); delete "
            f"{marker} by hand and re-run"
        ) from exc
    return True


def _refuse_unless_stale(marker: Path, holder: Holder, repo_id: str) -> None:
    """Return only when the holder is provably gone: same host, dead pid."""
    # Anyone with write access to the share can write a marker.
    host = clean_text(holder.hostname, single_line=True)
    started = clean_text(holder.started, single_line=True)
    held = (
        f"another pull of {repo_id} is in progress (host {host}, pid {holder.pid}, "
        f"started {started}); its marker is {marker}"
    )
    if holder.hostname != _this_host():
        raise PullInProgressError(
            f"{held}. It was started on another machine, so this tool cannot tell "
            "whether it is still running; if it is not, delete that marker file and re-run"
        )
    # This run has not claimed yet, so a marker naming its own pid is
    # another process's leftover: a container whose entrypoint is always
    # pid 1 would otherwise refuse itself after every hard kill.
    if holder.pid != os.getpid() and _holder_running(holder.pid):
        raise PullInProgressError(
            f"{held}. Wait for it to finish; if no pull of {repo_id} is running "
            "(the pid now belongs to another process), delete that marker file and re-run"
        )


def _holder_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # PermissionError: alive, another user's; anything else: assume alive
    return True


def _release(marker: Path, own: bytes) -> None:
    """Remove this run's marker and an emptied leaf.

    A closed terminal can deliver SIGHUP twice, and a second signal
    landing in here would strand the marker for every other host. So an
    interrupt mid-release finishes the release, then propagates. Raises
    nothing else.
    """
    try:
        _release_once(marker, own)
    except KeyboardInterrupt:
        _release_once(marker, own)
        raise


def _release_once(marker: Path, own: bytes) -> None:
    try:
        if read_marker_bytes(marker) == own:
            marker.unlink()
    except OSError:
        pass  # already gone, or unreadable: never guess
    discard_empty_staging_leaf(marker.parent)
