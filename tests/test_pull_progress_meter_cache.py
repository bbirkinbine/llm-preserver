"""Meter review-round fixes: files copied from the local HF cache (spec 0026).

Criterion 3 (amended 2026-11-06): a file counts as copied from the cache
when the probe found it there before the download, staging did not
already hold it, and the download moved zero bytes; its declared size
counts as done like a reused file's. Staging takes precedence: when it
held the file *and* the cache had it, the file is reused, because the
client checks staging first and falls back to the cache only when the
staged copy is stale (review-round decisions). While a copy is in flight
the phase is ``copying``.
"""

from llm_preserver.pull_progress import PullProgress


class FakeClock:
    """A fixed monotonic clock; these tests never advance time."""

    def __call__(self) -> float:
        return 100.0


def _begun() -> PullProgress:
    meter = PullProgress(clock=FakeClock())
    meter.begin([("a.gguf", 1000), ("b.gguf", 2000)])
    return meter


# --- 6. start_file: the copying phase ---------------------------------------


def test_a_cached_file_not_in_staging_starts_in_the_copying_phase() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False, cached_locally=True)
    assert meter.snapshot().phase == "copying"


def test_a_cached_file_already_in_staging_starts_downloading_because_staging_wins() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=True, cached_locally=True)
    assert meter.snapshot().phase == "downloading"


def test_an_explicitly_uncached_file_starts_downloading() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False, cached_locally=False)
    assert meter.snapshot().phase == "downloading"


def test_the_next_file_without_the_flag_is_back_to_downloading() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False, cached_locally=True)
    meter.finish_file(1000)
    meter.start_file(2, "b.gguf", declared_size=2000, already_staged=False)
    assert meter.snapshot().phase == "downloading"


# --- 7. finish_file: copied vs reused vs neither ----------------------------


def test_cached_unstaged_file_moving_zero_bytes_is_copied_and_its_size_credited() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False, cached_locally=True)
    meter.finish_file(1000)
    snap = meter.snapshot()
    assert snap.copied_files == 1
    assert snap.copied_bytes == 1000
    assert snap.reused_files == 0
    assert snap.reused_bytes == 0
    assert snap.network_bytes == 0
    assert snap.done_bytes == 1000
    assert snap.archived_bytes == 1000


def test_staged_and_cached_file_moving_zero_bytes_is_reused_not_copied() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=True, cached_locally=True)
    meter.finish_file(1000)
    snap = meter.snapshot()
    assert snap.reused_files == 1
    assert snap.reused_bytes == 1000
    assert snap.copied_files == 0
    assert snap.copied_bytes == 0
    assert snap.done_bytes == 1000


def test_zero_bytes_with_neither_signal_is_neither_copied_nor_reused() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False, cached_locally=False)
    meter.finish_file(1000)
    snap = meter.snapshot()
    assert snap.copied_files == 0
    assert snap.copied_bytes == 0
    assert snap.reused_files == 0
    assert snap.done_bytes == 0


def test_cached_file_that_still_moved_bytes_is_not_copied() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False, cached_locally=True)
    meter.add_transfer_bytes(1000)
    meter.finish_file(1000)
    snap = meter.snapshot()
    assert snap.copied_files == 0
    assert snap.copied_bytes == 0
    assert snap.reused_files == 0
    assert snap.done_bytes == 1000


def test_copy_is_judged_on_this_files_bytes_and_flags_not_the_whole_pull() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.add_transfer_bytes(1000)
    meter.finish_file(1000)
    meter.start_file(2, "b.gguf", declared_size=2000, already_staged=False, cached_locally=True)
    meter.finish_file(2000)
    snap = meter.snapshot()
    assert snap.copied_files == 1
    assert snap.copied_bytes == 2000
    assert snap.network_bytes == 1000
    assert snap.done_bytes == 3000


def test_a_cached_flag_does_not_leak_into_the_next_file() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False, cached_locally=True)
    meter.finish_file(1000)
    meter.start_file(2, "b.gguf", declared_size=2000, already_staged=False)
    meter.finish_file(2000)
    snap = meter.snapshot()
    assert snap.copied_files == 1
    assert snap.copied_bytes == 1000
    assert snap.done_bytes == 1000


def test_copied_counts_start_at_zero() -> None:
    snap = _begun().snapshot()
    assert snap.copied_files == 0
    assert snap.copied_bytes == 0
