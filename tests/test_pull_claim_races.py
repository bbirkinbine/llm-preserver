"""Spec 0025: the claim's retries, its failure cleanup, and safe reads.

The review round found these guards survived deletion with the rest of
the suite green: the ``ENOENT`` retry when a sibling's owner-directory
``rmdir`` lands between our ``mkdir`` and our create, the retry when a
holder releases between our ``EEXIST`` and our read, removal of a
half-written marker, and the release path reading through a FIFO or a
stale handle. Each test here fails with its guard removed.

FIFO tests need ``os.mkfifo`` (POSIX); they skip where it is absent.
"""

import errno
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from pull_claim_shapes import DEAD_PID, MARKER_NAME, estale_on_read, holder, pin_host, plant
from staging_shapes import REPO_ID

from llm_preserver.hub import PullEnvError, PullUserError, RepoFile

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")


@pytest.fixture
def leaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pin_host(monkeypatch)
    return tmp_path / "archive" / ".staging" / "bartowski" / "tiny-chat-GGUF"


def claim_once(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    with claim_pull(leaf, REPO_ID):
        assert (leaf / MARKER_NAME).is_file()


def finishes_within(seconds: float, action: Any) -> bool:  # Any: a no-arg callable
    """Run ``action`` on a daemon thread; False if it is still blocked."""
    worker = threading.Thread(target=action, daemon=True)
    worker.start()
    worker.join(seconds)
    return not worker.is_alive()


# --- the claim's retries ----------------------------------------------------


def test_create_retries_when_the_leaf_vanishes_under_it(
    leaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sibling's owner-dir rmdir between mkdir and create is retried, not raised."""
    real_open = os.open
    failures = []

    def vanish_once(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_EXCL and not failures:
            failures.append(path)
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", vanish_once)

    claim_once(leaf)

    assert failures, "positive control: the create did hit ENOENT once"


def test_holder_releasing_between_create_and_read_is_retried(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = leaf / MARKER_NAME
    plant(marker, holder(tmp_path, pid=os.getpid()))  # live: would refuse if read
    real_open = os.open
    released = []

    def release_before_read(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(os.fsdecode(path)) == marker and not flags & os.O_CREAT and not released:
            released.append(True)
            marker.unlink()  # the holder finishes at exactly this moment
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", release_before_read)

    claim_once(leaf)

    assert released, "positive control: the read did race the holder's release"


def test_a_failed_marker_write_leaves_no_marker_behind(
    leaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty marker would refuse every later pull as malformed."""
    from llm_preserver.pull_claim import claim_pull

    real_write = os.write

    def disk_full(fd: int, data: bytes) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "write", disk_full)
    with (
        pytest.raises(PullEnvError, match="cannot create the pull marker"),
        claim_pull(leaf, REPO_ID),
    ):
        pass

    assert not (leaf / MARKER_NAME).exists()
    monkeypatch.setattr(os, "write", real_write)
    claim_once(leaf)  # and the next pull is not blocked by debris


# --- special files at the marker's path -------------------------------------


@needs_fifo
def test_a_fifo_at_the_marker_path_is_refused_without_blocking(leaf: Path) -> None:
    from llm_preserver.pull_claim import PullInProgressError

    leaf.mkdir(parents=True)
    os.mkfifo(leaf / MARKER_NAME)
    outcome: list[BaseException] = []

    def attempt() -> None:
        try:
            claim_once(leaf)
        except PullInProgressError as exc:
            outcome.append(exc)

    assert finishes_within(5, attempt), "the claim blocked opening a FIFO"
    assert outcome and "not a regular file" in str(outcome[0])
    assert (leaf / MARKER_NAME).exists()


def test_a_directory_at_the_marker_path_is_refused(leaf: Path) -> None:
    from llm_preserver.pull_claim import PullInProgressError

    (leaf / MARKER_NAME).mkdir(parents=True)

    with pytest.raises(PullInProgressError, match="not a regular file"):
        claim_once(leaf)
    assert (leaf / MARKER_NAME).is_dir()


@needs_fifo
def test_stale_takeover_does_not_block_when_the_marker_became_a_fifo(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Judged stale, then swapped for a FIFO before the re-read: refused."""
    from llm_preserver.pull_claim import PullInProgressError

    marker = leaf / MARKER_NAME
    plant(marker, holder(tmp_path))
    real_kill = os.kill

    def kill_then_swap(pid: int, sig: int) -> None:
        if pid == DEAD_PID:
            marker.unlink()
            os.mkfifo(marker)
            raise ProcessLookupError(pid)
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", kill_then_swap)
    outcome: list[BaseException] = []

    def attempt() -> None:
        try:
            claim_once(leaf)
        except PullInProgressError as exc:
            outcome.append(exc)

    assert finishes_within(5, attempt), "the takeover blocked re-reading a FIFO"
    assert outcome and "not a regular file" in str(outcome[0])


# --- release never blocks, never raises -------------------------------------


@needs_fifo
def test_release_does_not_block_when_the_marker_became_a_fifo(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    marker = leaf / MARKER_NAME

    def swap_then_release() -> None:
        with claim_pull(leaf, REPO_ID):
            marker.unlink()
            os.mkfifo(marker)

    assert finishes_within(5, swap_then_release), "release blocked reading a FIFO"
    assert marker.exists()  # not ours, so never removed


def test_release_survives_a_stale_handle_and_leaves_the_marker(
    leaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_preserver.pull_claim import claim_pull

    marker = leaf / MARKER_NAME
    with claim_pull(leaf, REPO_ID):
        estale_on_read(monkeypatch, marker)

    assert marker.exists()  # unreadable: never guess it is ours


# --- a root *directory* named like the marker -------------------------------


def test_root_directory_named_like_the_marker_is_refused() -> None:
    from llm_preserver.pull_claim import require_no_marker_collision

    nested = RepoFile(path=f"{MARKER_NAME.upper()}/x-Q4_K_M.gguf", size=10, sha256=None)
    with pytest.raises(PullUserError):
        require_no_marker_collision([nested])
