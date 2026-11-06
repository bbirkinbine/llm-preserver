"""Spec 0026 pass 4: no-network credit, no-start paths, and invariance.

Criterion 3 at the core seam: the transfer loop tells the meter, per
file, whether staging already held it at its declared size (the same
per-file rule as ``pull_preflight.already_staged_bytes``) and whether
the local Hugging Face cache held it, so a file that moved zero network
bytes is credited as reused or copied only on that evidence. Pulls with
nothing to transfer never begin the meter (pass 5 prints the summary
only when it started). Criterion 6: a meter never changes the record,
the manifest, the archived bytes, or a failure.

``FakeHubClient`` (``tests/fake_hub.py``) serves bytes from memory;
archives live in ``tmp_path``.
"""

from pathlib import Path
from typing import Any

import pytest
from progress_wiring_shapes import (
    HUB_FILES,
    Q4_BYTES,
    Q4_NAME,
    Q8_NAME,
    RecordingProgress,
    archive_tree,
    do_pull,
    model_dir,
    staging_leaf,
)
from staging_shapes import new_archive

import llm_preserver.hub as hub
from llm_preserver.archive import init_archive


def _client(fake_hub_factory: Any, **overrides: Any) -> Any:
    overrides.setdefault("files", HUB_FILES)
    return fake_hub_factory(**overrides)


def _q4_start(meter: RecordingProgress) -> tuple[Any, ...]:
    return next(e for e in meter.events if e[0] == "start_file" and e[2] == Q4_NAME)


def _stage(root: Path, content: bytes) -> None:
    """Leave a previous attempt's Q4 in the staging leaf."""
    staged = staging_leaf(root) / Q4_NAME
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(content)


# --- reused from staging: the declared-size cross-check -------------------


def test_file_staged_at_declared_size_is_reported_staged_and_credited_reused(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    root = new_archive(tmp_path)
    _stage(root, Q4_BYTES)
    meter = RecordingProgress()
    client = _client(fake_hub_factory, progress_chunks={Q4_NAME: []})

    do_pull(root, client, progress=meter)

    assert _q4_start(meter) == ("start_file", 1, Q4_NAME, len(Q4_BYTES), True, False)
    snap = meter.snapshot()
    assert snap.reused_files == 1
    assert snap.reused_bytes == len(Q4_BYTES)


def test_file_staged_at_the_wrong_size_is_not_reported_staged(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    root = new_archive(tmp_path)
    _stage(root, b"short")
    meter = RecordingProgress()
    client = _client(fake_hub_factory, progress_chunks={Q4_NAME: []})

    do_pull(root, client, progress=meter)

    assert _q4_start(meter)[4] is False
    assert meter.snapshot().reused_files == 0


def test_staged_file_that_still_moved_bytes_is_not_credited_reused(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    root = new_archive(tmp_path)
    _stage(root, Q4_BYTES)
    meter = RecordingProgress()

    do_pull(root, _client(fake_hub_factory), progress=meter)

    assert _q4_start(meter)[4] is True
    assert meter.snapshot().reused_files == 0


# --- copied from the local Hugging Face cache -----------------------------


def test_cached_unstaged_file_that_moved_no_bytes_is_credited_copied(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    meter = RecordingProgress()
    client = _client(fake_hub_factory, cached_paths={Q4_NAME}, progress_chunks={Q4_NAME: []})

    do_pull(new_archive(tmp_path), client, progress=meter)

    assert _q4_start(meter) == ("start_file", 1, Q4_NAME, len(Q4_BYTES), False, True)
    snap = meter.snapshot()
    assert snap.copied_files == 1
    assert snap.copied_bytes == len(Q4_BYTES)
    assert snap.reused_files == 0


def test_file_both_staged_and_cached_is_credited_reused_not_copied(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    root = new_archive(tmp_path)
    _stage(root, Q4_BYTES)
    meter = RecordingProgress()
    client = _client(fake_hub_factory, cached_paths={Q4_NAME}, progress_chunks={Q4_NAME: []})

    do_pull(root, client, progress=meter)

    assert _q4_start(meter)[4:] == (True, True)
    snap = meter.snapshot()
    assert (snap.reused_files, snap.copied_files) == (1, 0)


# --- nothing to transfer: the meter never begins --------------------------


def test_no_op_repull_never_begins_the_meter(tmp_path: Path, fake_hub_factory: Any) -> None:
    root = new_archive(tmp_path)
    do_pull(root, _client(fake_hub_factory))
    meter = RecordingProgress()

    do_pull(root, _client(fake_hub_factory), progress=meter)

    assert meter.events == []
    assert not meter.started


def test_metadata_only_repull_never_begins_the_meter(tmp_path: Path, fake_hub_factory: Any) -> None:
    root = new_archive(tmp_path)
    do_pull(root, _client(fake_hub_factory))
    meter = RecordingProgress()
    recorded: list[bool] = []

    do_pull(
        root,
        _client(fake_hub_factory),
        roles=["embedding"],
        on_metadata_only=lambda: recorded.append(True),
        progress=meter,
    )

    assert recorded == [True]
    assert meter.events == []
    assert not meter.started


def test_adopt_only_pull_never_begins_the_meter(tmp_path: Path, fake_hub_factory: Any) -> None:
    root = new_archive(tmp_path)
    on_disk = model_dir(root) / "gguf" / Q4_NAME
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(Q4_BYTES)
    client = fake_hub_factory(files=[(Q4_NAME, Q4_BYTES, True)])
    meter = RecordingProgress()

    do_pull(root, client, progress=meter)

    assert client.download_calls == []
    assert (model_dir(root) / "model-record.json").is_file()  # adoption recorded
    assert meter.events == []
    assert not meter.started


# --- invariance: a meter changes nothing that lands (criterion 6) ---------


def _two_archives(tmp_path: Path) -> tuple[Path, Path]:
    plain, metered = tmp_path / "plain", tmp_path / "metered"
    init_archive(plain)
    init_archive(metered)
    return plain, metered


def test_meter_changes_no_record_manifest_or_archived_byte(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    plain, metered = _two_archives(tmp_path)
    include = [f"*{Q4_NAME}", f"*{Q8_NAME}"]

    landed_plain = do_pull(plain, _client(fake_hub_factory), include=include)
    landed_metered = do_pull(
        metered, _client(fake_hub_factory), include=include, progress=RecordingProgress()
    )

    assert landed_plain.relative_to(plain) == landed_metered.relative_to(metered)
    # The record carries only a download *date*, the same in both runs.
    tree = archive_tree(plain)
    assert "bartowski/tiny-chat-GGUF/model-record.json" in tree
    assert "bartowski/tiny-chat-GGUF/manifest-sha256.txt" in tree
    assert archive_tree(metered) == tree


def test_hash_mismatch_with_a_meter_fails_and_discards_exactly_as_without(
    tmp_path: Path, fake_hub_factory: Any
) -> None:
    plain, metered = _two_archives(tmp_path)
    corrupt = {Q4_NAME: b"corrupted bytes"}

    with pytest.raises(hub.PullIntegrityError) as without_meter:
        do_pull(plain, _client(fake_hub_factory, served_bytes=corrupt))
    with pytest.raises(hub.PullIntegrityError) as with_meter:
        do_pull(
            metered,
            _client(fake_hub_factory, served_bytes=corrupt),
            progress=RecordingProgress(),
        )

    assert str(with_meter.value) == str(without_meter.value)
    leaf = staging_leaf(metered)
    assert not (leaf / Q4_NAME).exists()
    assert not (leaf / ".cache" / "huggingface" / "download" / f"{Q4_NAME}.metadata").exists()
    assert archive_tree(metered) == archive_tree(plain) == {}


# --- the staging check's per-file rule -------------------------------------


def test_a_staged_file_with_no_declared_size_never_counts_as_staged(tmp_path: Path) -> None:
    # Same rule as pull_preflight.already_staged_bytes: with no declared
    # size nothing says the staged bytes are complete, so a partial file
    # must not be credited as reused.
    from llm_preserver.hub import RepoFile
    from llm_preserver.pull_plan import PlannedDownload
    from llm_preserver.pull_transfer import _staged_at_declared_size

    (tmp_path / "w.gguf").write_bytes(b"partial")
    sizeless = PlannedDownload(RepoFile("w.gguf", None, None), "gguf/w.gguf")
    sized = PlannedDownload(RepoFile("w.gguf", 7, None), "gguf/w.gguf")

    assert _staged_at_declared_size(tmp_path, sizeless) is False
    assert _staged_at_declared_size(tmp_path, sized) is True
