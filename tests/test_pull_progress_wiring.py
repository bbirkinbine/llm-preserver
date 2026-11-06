"""Spec 0026 pass 4: ``pull_model`` and the transfer loop drive the meter.

Criteria 1-4 at the core seam, below the CLI: with a meter, the pull
announces its plan once (when bytes can start moving, never during a
prompt), then for each planned download tells the meter which file is
in flight and where its bytes could come from, hands the meter to the
hub client as the transfer sink, and feeds the hash's byte counter.
Without a meter every call is exactly today's (criterion 4): the
``download`` call carries no ``progress`` key at all, the local-cache
probe never runs, and ``sha256_of`` takes no ``progress`` keyword.

``FakeHubClient`` (``tests/fake_hub.py``) serves bytes from memory and
reports them to a given sink; archives live in ``tmp_path``.
"""

from pathlib import Path
from typing import Any

import pytest
from progress_wiring_shapes import (
    COMMIT,
    HUB_FILES,
    Q4_BYTES,
    Q4_NAME,
    Q8_BYTES,
    Q8_NAME,
    README_BYTES,
    README_NAME,
    REPO_ID,
    RecordingProgress,
    do_pull,
    model_dir,
)
from staging_shapes import new_archive

import llm_preserver.hashing as hashing
import llm_preserver.pull_transfer as pull_transfer

_DOWNLOAD_KEYS = {"repo_id", "filename", "revision", "dest_dir"}


def _client(fake_hub_factory: Any, **overrides: Any) -> Any:
    overrides.setdefault("files", HUB_FILES)
    return fake_hub_factory(**overrides)


# --- no meter: every call is exactly today's (criterion 4) ---------------


def test_pull_without_a_meter_passes_no_progress_key_to_download(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    client = _client(fake_hub_factory)
    do_pull(new_archive(tmp_path), client)

    assert [call["filename"] for call in client.download_kwargs] == [Q4_NAME, README_NAME]
    for call in client.download_kwargs:
        assert set(call) == _DOWNLOAD_KEYS  # not even progress=None


def test_pull_without_a_meter_never_probes_the_local_cache(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    client = _client(fake_hub_factory, cached_paths={Q4_NAME})
    do_pull(new_archive(tmp_path), client)

    assert client.cached_locally_calls == []


def test_pull_without_a_meter_hashes_staged_files_with_no_progress_keyword(
    tmp_path: Path, fake_hub_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    real = hashing.sha256_of

    def spy(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    # Patched where the transfer loop binds it and at the module seam,
    # so the guard holds whichever way the call resolves.
    monkeypatch.setattr(pull_transfer, "sha256_of", spy, raising=False)
    monkeypatch.setattr(hashing, "sha256_of", spy)
    root = new_archive(tmp_path)
    do_pull(root, _client(fake_hub_factory))

    staged = [(args, kwargs) for args, kwargs in calls if ".staging" in str(args[0])]
    assert [Path(args[0]).name for args, _ in staged] == [Q4_NAME, README_NAME]
    for args, kwargs in staged:
        assert len(args) == 1
        assert kwargs == {}


# --- begin: once, with the plan, when bytes can start moving --------------


def test_meter_begins_once_with_planned_downloads_and_their_declared_sizes(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    meter = RecordingProgress()
    do_pull(new_archive(tmp_path), _client(fake_hub_factory), progress=meter)

    begins = [event for event in meter.events if event[0] == "begin"]
    assert begins == [("begin", [(Q4_NAME, len(Q4_BYTES)), (README_NAME, len(README_BYTES))], 0)]
    assert meter.started


def test_meter_begin_carries_the_adopted_files_bytes(tmp_path: Path, fake_hub_factory: Any) -> None:
    root = new_archive(tmp_path)
    # An unrecorded on-disk Q4 whose hash matches the hub is adopted.
    on_disk = model_dir(root) / "gguf" / Q4_NAME
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(Q4_BYTES)
    meter = RecordingProgress()
    client = _client(fake_hub_factory)

    do_pull(root, client, include=[f"*{Q4_NAME}", f"*{Q8_NAME}"], progress=meter)

    assert client.download_calls == [Q8_NAME, README_NAME]
    assert meter.events[0] == (
        "begin",
        [(Q8_NAME, len(Q8_BYTES)), (README_NAME, len(README_BYTES))],
        len(Q4_BYTES),
    )
    assert meter.snapshot().adopted_bytes == len(Q4_BYTES)


def test_meter_has_not_begun_while_the_size_confirmation_is_asked(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    meter = RecordingProgress()
    started_at_prompt: list[bool] = []

    def confirm(prompt: str) -> bool:
        started_at_prompt.append(meter.started)
        return True

    do_pull(new_archive(tmp_path), _client(fake_hub_factory), confirm=confirm, progress=meter)

    assert started_at_prompt  # the size confirmation was asked
    assert not any(started_at_prompt)
    assert meter.started


def test_meter_begins_before_the_transfer_start_seam_and_before_any_file(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    meter = RecordingProgress()

    def on_transfer_start(model: str) -> None:
        meter.events.append(("on_transfer_start",))

    do_pull(
        new_archive(tmp_path),
        _client(fake_hub_factory),
        on_transfer_start=on_transfer_start,
        progress=meter,
    )

    assert meter.names()[:3] == ["begin", "on_transfer_start", "start_file"]


# --- per file: start, transfer through the sink, hash, finish -------------


def test_each_planned_download_runs_start_transfer_hash_finish_in_order(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    meter = RecordingProgress()
    do_pull(new_archive(tmp_path), _client(fake_hub_factory), progress=meter)

    q4, readme = len(Q4_BYTES), len(README_BYTES)
    assert meter.events[1:] == [
        ("start_file", 1, Q4_NAME, q4, False, False),
        ("transfer", q4),
        ("start_hash", q4),
        ("hash", q4, "hashing"),
        ("finish_file", q4),
        ("start_file", 2, README_NAME, readme, False, False),
        ("transfer", readme),
        ("start_hash", readme),
        ("hash", readme, "hashing"),
        ("finish_file", readme),
    ]


def test_the_meter_is_the_sink_handed_to_every_download(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    meter = RecordingProgress()
    client = _client(fake_hub_factory)
    do_pull(new_archive(tmp_path), client, progress=meter)

    assert [call["progress"] for call in client.download_kwargs] == [meter, meter]
    assert all(call["progress"] is meter for call in client.download_kwargs)


def test_the_cache_probe_asks_for_each_planned_file_at_the_pinned_commit(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    client = _client(fake_hub_factory)
    do_pull(new_archive(tmp_path), client, progress=RecordingProgress())

    assert client.cached_locally_calls == [
        (REPO_ID, Q4_NAME, COMMIT),
        (REPO_ID, README_NAME, COMMIT),
    ]


def test_hash_and_finish_use_the_downloaded_file_size_not_the_declared_one(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    # README publishes no hash, so different served bytes still archive;
    # the meter must count what is on disk, not what the hub declared.
    served = b"a longer readme than the hub declared\n"
    meter = RecordingProgress()
    client = _client(fake_hub_factory, served_bytes={README_NAME: served})
    do_pull(new_archive(tmp_path), client, progress=meter)

    readme_start = meter.events.index(
        ("start_file", 2, README_NAME, len(README_BYTES), False, False)
    )
    tail = meter.events[readme_start:]
    assert ("start_hash", len(served)) in tail
    assert tail[-1] == ("finish_file", len(served))
    assert meter.snapshot().archived_bytes == len(Q4_BYTES) + len(served)


def test_hash_bytes_reach_the_meter_and_end_complete(tmp_path: Path, fake_hub_factory: Any) -> None:
    meter = RecordingProgress()
    do_pull(new_archive(tmp_path), _client(fake_hub_factory), progress=meter)

    hashed = [event for event in meter.events if event[0] == "hash"]
    assert sum(event[1] for event in hashed) == len(Q4_BYTES) + len(README_BYTES)
    assert {event[2] for event in hashed} == {"hashing"}
    snap = meter.snapshot()
    assert snap.hash_done == len(README_BYTES)
    assert snap.hash_total == len(README_BYTES)


def test_a_retried_transfers_negative_increment_reaches_the_meter(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    meter = RecordingProgress()
    client = _client(fake_hub_factory, progress_chunks={Q4_NAME: [10, -10, len(Q4_BYTES)]})
    do_pull(new_archive(tmp_path), client, progress=meter)

    snap = meter.snapshot()
    assert snap.network_bytes == len(Q4_BYTES) + len(README_BYTES)
    assert snap.reused_files == 0
    assert snap.copied_files == 0
