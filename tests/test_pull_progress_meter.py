"""Tests for the pull progress meter state object (spec 0026, pass 2).

``PullProgress`` is the one lock-guarded state the hub client's
callback thread writes into and the ticker reads from. It is pure: an
injected clock, no I/O, no threads of its own. The byte handlers run
inside hf_xet's callback and must never raise (criterion 6), so every
non-int value is ignored rather than rejected. Reuse needs both
signals (criterion 3, plan checkpoint): the file was in staging at its
declared size beforehand *and* this file's download moved zero bytes.
"""

import dataclasses
import threading

import pytest

from llm_preserver.pull_progress import ProgressSnapshot, PullProgress

GIB = 1024**3


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _meter(clock: FakeClock | None = None) -> PullProgress:
    return PullProgress(clock=clock or FakeClock())


def _begun(
    files: list[tuple[str, int | None]] | None = None, *, adopted_bytes: int = 0
) -> PullProgress:
    meter = _meter()
    meter.begin(files or [("a.gguf", 1000), ("b.gguf", 2000)], adopted_bytes=adopted_bytes)
    return meter


# --- begin: totals, floor flag, start time ---------------------------------


def test_started_is_false_until_begin_is_called() -> None:
    meter = _meter()
    assert meter.started is False
    meter.begin([("a.gguf", 10)])
    assert meter.started is True


def test_total_is_the_sum_of_declared_sizes_and_not_a_floor() -> None:
    snap = _begun([("a.gguf", 1000), ("b.gguf", 2000), ("c.gguf", 3)]).snapshot()
    assert snap.total == 3003
    assert snap.total_is_floor is False
    assert snap.file_count == 3


def test_any_unknown_size_makes_the_total_a_floor_of_the_known_sizes() -> None:
    snap = _begun([("a.gguf", 1000), ("b.gguf", None), ("c.gguf", 500)]).snapshot()
    assert snap.total == 1500
    assert snap.total_is_floor is True
    assert snap.file_count == 3


def test_started_at_is_the_clock_at_begin_and_now_is_the_clock_at_snapshot() -> None:
    clock = FakeClock(50.0)
    meter = PullProgress(clock=clock)
    meter.begin([("a.gguf", 10)])
    clock.value = 80.0
    snap = meter.snapshot()
    assert snap.started_at == 50.0
    assert snap.now == 80.0


def test_snapshot_is_a_frozen_progress_snapshot() -> None:
    snap = _begun().snapshot()
    assert isinstance(snap, ProgressSnapshot)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.total = 0  # type: ignore[misc]


def test_snapshot_is_a_copy_unaffected_by_later_updates() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    before = meter.snapshot()
    meter.add_transfer_bytes(400)
    assert before.network_bytes == 0
    assert meter.snapshot().network_bytes == 400


# --- start_file / start_hash: phase and n of m -----------------------------


def test_start_file_sets_downloading_phase_index_and_path() -> None:
    meter = _begun()
    meter.start_file(2, "sub/b.gguf", declared_size=2000, already_staged=False)
    snap = meter.snapshot()
    assert snap.phase == "downloading"
    assert snap.index == 2
    assert snap.path == "sub/b.gguf"


def test_start_hash_switches_to_hashing_and_counts_hash_bytes() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.start_hash(1000)
    meter.add_hash_bytes(300)
    meter.add_hash_bytes(110)
    snap = meter.snapshot()
    assert snap.phase == "hashing"
    assert snap.hash_total == 1000
    assert snap.hash_done == 410


def test_start_hash_restarts_the_hash_count_for_the_next_file() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.start_hash(1000)
    meter.add_hash_bytes(1000)
    meter.finish_file(1000)
    meter.start_file(2, "b.gguf", declared_size=2000, already_staged=False)
    assert meter.snapshot().phase == "downloading"
    meter.start_hash(2000)
    snap = meter.snapshot()
    assert snap.hash_done == 0
    assert snap.hash_total == 2000


# --- add_transfer_bytes: signed sum, clamp, never raises -------------------


def test_transfer_bytes_accumulate_into_network_and_done_bytes() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.add_transfer_bytes(250)
    meter.add_transfer_bytes(250)
    snap = meter.snapshot()
    assert snap.network_bytes == 500
    assert snap.done_bytes == 500


def test_negative_increment_from_a_ranged_retry_reduces_the_count() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.add_transfer_bytes(600)
    meter.add_transfer_bytes(-200)
    assert meter.snapshot().network_bytes == 400


def test_network_bytes_display_clamps_at_zero_but_the_sum_stays_signed() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.add_transfer_bytes(100)
    meter.add_transfer_bytes(-300)
    assert meter.snapshot().network_bytes == 0
    assert meter.snapshot().done_bytes == 0
    meter.add_transfer_bytes(250)
    assert meter.snapshot().network_bytes == 50


@pytest.mark.parametrize("bad", [None, "5", 1.5, True, False, object(), [3]])
def test_non_int_transfer_values_are_ignored_without_raising(bad: object) -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.add_transfer_bytes(10)
    meter.add_transfer_bytes(bad)
    assert meter.snapshot().network_bytes == 10


@pytest.mark.parametrize("bad", [None, "5", 1.5, True, object()])
def test_non_int_hash_values_are_ignored_without_raising(bad: object) -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.start_hash(1000)
    meter.add_hash_bytes(7)
    meter.add_hash_bytes(bad)
    assert meter.snapshot().hash_done == 7


def test_byte_handlers_do_not_raise_before_begin() -> None:
    meter = _meter()
    meter.add_transfer_bytes(10)
    meter.add_hash_bytes(10)
    meter.add_transfer_bytes(None)


def test_concurrent_transfer_callbacks_sum_exactly() -> None:
    meter = _begun([("a.gguf", 10**9)])
    meter.start_file(1, "a.gguf", declared_size=10**9, already_staged=False)
    threads_n, calls = 8, 5000

    def pump() -> None:
        for _ in range(calls):
            meter.add_transfer_bytes(3)

    threads = [threading.Thread(target=pump) for _ in range(threads_n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert meter.snapshot().network_bytes == threads_n * calls * 3


# --- finish_file: archived bytes and reuse ---------------------------------


def test_finish_file_adds_archived_bytes_across_files() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.add_transfer_bytes(900)
    meter.finish_file(1000)
    meter.start_file(2, "b.gguf", declared_size=2000, already_staged=False)
    meter.add_transfer_bytes(2000)
    meter.finish_file(2000)
    snap = meter.snapshot()
    assert snap.archived_bytes == 3000
    assert snap.network_bytes == 2900


def test_staged_file_moving_zero_bytes_is_reused_and_its_size_credited() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=True)
    meter.finish_file(1000)
    snap = meter.snapshot()
    assert snap.reused_files == 1
    assert snap.reused_bytes == 1000
    assert snap.network_bytes == 0
    assert snap.done_bytes == 1000
    assert snap.archived_bytes == 1000


def test_zero_bytes_without_staging_evidence_is_not_reuse() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.finish_file(1000)
    snap = meter.snapshot()
    assert snap.reused_files == 0
    assert snap.reused_bytes == 0
    assert snap.done_bytes == 0


def test_staged_file_that_still_moved_bytes_is_not_reuse() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=True)
    meter.add_transfer_bytes(1000)
    meter.finish_file(1000)
    snap = meter.snapshot()
    assert snap.reused_files == 0
    assert snap.reused_bytes == 0
    assert snap.done_bytes == 1000


def test_reuse_is_judged_on_this_files_bytes_not_the_whole_pull() -> None:
    meter = _begun()
    meter.start_file(1, "a.gguf", declared_size=1000, already_staged=False)
    meter.add_transfer_bytes(1000)
    meter.finish_file(1000)
    meter.start_file(2, "b.gguf", declared_size=2000, already_staged=True)
    meter.finish_file(2000)
    snap = meter.snapshot()
    assert snap.reused_files == 1
    assert snap.reused_bytes == 2000
    assert snap.network_bytes == 1000
    assert snap.done_bytes == 3000


# --- adopted bytes: counted apart from the network ------------------------


def test_adopted_bytes_are_reported_separately_and_not_as_network_or_done() -> None:
    snap = _begun([("a.gguf", 1000)], adopted_bytes=5 * GIB).snapshot()
    assert snap.adopted_bytes == 5 * GIB
    assert snap.network_bytes == 0
    assert snap.done_bytes == 0
    assert snap.total == 1000


def test_adopted_bytes_default_to_zero() -> None:
    assert _begun().snapshot().adopted_bytes == 0
