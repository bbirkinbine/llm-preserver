"""The pull's progress state: one lock-guarded object (spec 0026).

The hub client calls :meth:`PullProgress.add_transfer_bytes` from its
own progress-callback thread (inside hf_xet for Xet transfers), the
transfer loop moves the phase forward on the main thread, and a ticker
thread reads :meth:`PullProgress.snapshot` to print a line. Every
mutation and the snapshot take the same lock, so a line never mixes two
moments.

The byte handlers never raise. They run inside the download client's
callback, where an exception could fail a download the archive needed
(criterion 6): a value this object does not understand is dropped,
which costs at most a less precise line.
"""

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

Phase = Literal["downloading", "hashing", "copying"]


@dataclass(frozen=True)
class ProgressSnapshot:
    """One consistent moment of a pull's progress, for rendering.

    Attributes:
        total: Sum of the hub-declared sizes of the planned downloads.
        total_is_floor: True when any planned file has no declared size,
            so ``total`` undercounts.
        file_count: Number of planned downloads (the ``m`` of ``n of m``).
        index: The file in flight, 1-based; 0 before the first file.
        path: The hub path of the file in flight.
        phase: ``downloading``, ``hashing``, or ``copying`` (a file the
            client is copying from the local Hugging Face cache).
        network_bytes: Bytes received over the network, never negative.
        done_bytes: ``network_bytes`` plus the declared sizes of files
            reused from staging or copied from the local cache; what
            the percentage counts.
        archived_bytes: Bytes of the files the pull finished.
        reused_files: Files reused from a previous attempt's staging.
        reused_bytes: Declared bytes of those reused files.
        adopted_bytes: Bytes already on disk that the plan adopted.
        hash_done: Bytes hashed so far of the file in flight.
        hash_total: Size being hashed, or None when unknown.
        started_at: Clock reading when the pull began.
        now: Clock reading when this snapshot was taken.
        copied_files: Files the client copied from the local Hugging
            Face cache instead of the network.
        copied_bytes: Declared bytes of those copied files.
    """

    total: int
    total_is_floor: bool
    file_count: int
    index: int
    path: str
    phase: Phase
    network_bytes: int
    done_bytes: int
    archived_bytes: int
    reused_files: int
    reused_bytes: int
    adopted_bytes: int
    hash_done: int
    hash_total: int | None
    started_at: float
    now: float
    copied_files: int = 0
    copied_bytes: int = 0


def _byte_count(value: object) -> int | None:
    """Return ``value`` when it is a plain ``int``, else None.

    ``bool`` is an ``int`` subclass and is refused: a flag is not a byte
    count.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class PullProgress:
    """Thread-safe progress state for one pull.

    Args:
        clock: Monotonic seconds source; injected so tests never sleep.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._started = False
        self._total = 0
        self._total_is_floor = False
        self._file_count = 0
        self._index = 0
        self._path = ""
        self._phase: Phase = "downloading"
        self._network = 0
        self._file_network = 0
        self._credited = 0
        self._archived = 0
        self._reused_files = 0
        self._reused_bytes = 0
        self._copied_files = 0
        self._copied_bytes = 0
        self._adopted = 0
        self._hash_done = 0
        self._hash_total: int | None = None
        self._declared_size: int | None = None
        self._already_staged = False
        self._cached_locally = False
        self._started_at = 0.0

    @property
    def started(self) -> bool:
        """True once :meth:`begin` has run."""
        with self._lock:
            return self._started

    def begin(self, files: Sequence[tuple[str, int | None]], *, adopted_bytes: int = 0) -> None:
        """Record the planned downloads and start the clock.

        Args:
            files: Each planned download as (hub path, declared size).
            adopted_bytes: Bytes of on-disk files the plan adopted.
        """
        with self._lock:
            self._total = sum(size for _, size in files if size is not None)
            self._total_is_floor = any(size is None for _, size in files)
            self._file_count = len(files)
            self._adopted = adopted_bytes
            self._started_at = self._clock()
            self._started = True

    def start_file(
        self,
        index: int,
        path: str,
        *,
        declared_size: int | None,
        already_staged: bool,
        cached_locally: bool = False,
    ) -> None:
        """Mark a file's download as in flight.

        Args:
            index: 1-based position among the planned downloads.
            path: The file's hub path.
            declared_size: The hub-declared size, or None.
            already_staged: Whether staging held the file at its
                declared size before the download started.
            cached_locally: Whether the local Hugging Face cache held
                the file at this commit before the download started.
                Staging wins when both hold it: the client checks
                staging first.
        """
        with self._lock:
            self._index = index
            self._path = path
            self._phase = "copying" if cached_locally and not already_staged else "downloading"
            self._file_network = 0
            self._declared_size = declared_size
            self._already_staged = already_staged
            self._cached_locally = cached_locally

    def add_transfer_bytes(self, n: object) -> None:
        """Count network bytes; the hub client's callback. Never raises.

        Args:
            n: A signed byte increment; negative when a ranged retry
                restarts. Anything that is not an ``int`` is dropped.
        """
        count = _byte_count(n)
        if count is None:
            return
        with self._lock:
            self._network += count
            self._file_network += count

    def start_hash(self, total: int | None) -> None:
        """Move the file in flight to the hashing phase.

        Args:
            total: The number of bytes the hash will read, or None.
        """
        with self._lock:
            self._phase = "hashing"
            self._hash_done = 0
            self._hash_total = total

    def add_hash_bytes(self, n: object) -> None:
        """Count bytes the hash has read. Never raises.

        Args:
            n: A byte increment; anything that is not an ``int`` is
                dropped.
        """
        count = _byte_count(n)
        if count is None:
            return
        with self._lock:
            self._hash_done += count

    def finish_file(self, archived_bytes: int) -> None:
        """Close out the file in flight.

        A file that moved no network bytes is credited only when there
        is evidence of where it came from: reused when staging held it
        at its declared size beforehand, else copied when the local
        cache held it. Zero bytes alone also describes a client that
        stopped reporting, which must not credit every file.

        Args:
            archived_bytes: The size of the finished file.
        """
        with self._lock:
            self._archived += archived_bytes
            if self._file_network != 0:
                return
            credit = self._declared_size if self._declared_size is not None else 0
            if self._already_staged:
                self._reused_files += 1
                self._reused_bytes += credit
                self._credited += credit
            elif self._cached_locally:
                self._copied_files += 1
                self._copied_bytes += credit
                self._credited += credit

    def snapshot(self) -> ProgressSnapshot:
        """Return a frozen copy of the current state."""
        with self._lock:
            network = max(self._network, 0)
            return ProgressSnapshot(
                total=self._total,
                total_is_floor=self._total_is_floor,
                file_count=self._file_count,
                index=self._index,
                path=self._path,
                phase=self._phase,
                network_bytes=network,
                done_bytes=network + self._credited,
                archived_bytes=self._archived,
                reused_files=self._reused_files,
                reused_bytes=self._reused_bytes,
                adopted_bytes=self._adopted,
                hash_done=self._hash_done,
                hash_total=self._hash_total,
                started_at=self._started_at,
                now=self._clock(),
                copied_files=self._copied_files,
                copied_bytes=self._copied_bytes,
            )
