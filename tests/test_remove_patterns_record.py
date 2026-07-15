"""Core pattern-scoped removal (spec 0010): record surgery.

After a pattern removal the record is the part that must stay honest:
matched FileEntrys leave it, an emptied artifact drops, an emptied
format dir prunes, MODEL-RECORD.md and manifest-sha256.txt regenerate,
and ``verify`` passes ``valid``. Crash safety inverts the write
convention — the updated record lands *before* any unlink, so an
interrupted run leaves informational ``unrecorded`` files (finishable
by re-running the identical command), never a record naming missing
files. Match semantics live in test_remove_patterns.py.

``llm_preserver.remove`` does not exist yet (test-first): imports are
lazy inside test bodies; the expected red is ModuleNotFoundError per
test.
"""

import hashlib
import importlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.archive import init_archive
from llm_preserver.records import MANIFEST_FILENAME, RECORD_FILENAME, RENDERED_FILENAME
from llm_preserver.verify import verify_archive

MODEL_ID = "acme/tiny-chat"
Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"
Q4 = b"q4 weight bytes"
Q8_REL = "gguf/tiny-chat-Q8_0.gguf"
Q8 = b"q8 weight bytes"
HF_CONFIG_REL = "hf-snapshot/config.json"
HF_CONFIG = b'{"architectures": []}\n'


def remove_module():
    """Late import of the module under test (expected red: not yet written)."""
    return importlib.import_module("llm_preserver.remove")


def entry_for(rel_path: str, content: bytes) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    return {
        "path": rel_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "source": "original",
    }


def hf_artifact(entries: list[dict[str, object]]) -> dict[str, object]:
    """A second, hf-snapshot artifact for the emptied-artifact test."""
    return {
        "format": "hf-snapshot",
        "quantization": None,
        "source_repo": None,
        "revision": None,
        "download_date": None,
        "runtime_tested": None,
        "provenance": "hashed-locally",
        "files": entries,
    }


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    """An initialized (empty) archive under tmp_path."""
    root = tmp_path / "archive"
    init_archive(root)
    return root


@pytest.fixture
def build_model(
    write_model: Callable[..., Path], sample_record_dict: Callable[..., dict]
) -> Callable[..., Path]:
    """Create acme/tiny-chat with the given record entries and disk bytes."""

    def _build(
        archive: Path,
        entries: list[dict[str, object]],
        payloads: dict[str, bytes],
        extra_artifacts: list[dict[str, object]] | None = None,
    ) -> Path:
        record = sample_record_dict()
        record["artifacts"][0]["files"] = entries
        record["artifacts"].extend(extra_artifacts or [])
        model_dir = write_model(archive, record)
        for rel_path, content in payloads.items():
            target = model_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return model_dir

    return _build


def two_quants(build_model: Callable[..., Path], root: Path) -> Path:
    """The quant-swap shape: Q4 and Q8 recorded and on disk."""
    return build_model(
        root, [entry_for(Q4_REL, Q4), entry_for(Q8_REL, Q8)], {Q4_REL: Q4, Q8_REL: Q8}
    )


def recorded_paths(model_dir: Path) -> set[str]:
    """FileEntry paths straight from the on-disk record JSON."""
    data = json.loads((model_dir / RECORD_FILENAME).read_text(encoding="utf-8"))
    return {entry["path"] for artifact in data["artifacts"] for entry in artifact["files"]}


def test_record_surgery_regenerates_files_and_verify_passes_valid(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    model_dir = two_quants(build_model, archive_root)
    rm = remove_module()

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"]))

    assert not (model_dir / Q4_REL).exists()
    assert (model_dir / Q8_REL).read_bytes() == Q8
    assert recorded_paths(model_dir) == {Q8_REL}
    rendered = (model_dir / RENDERED_FILENAME).read_text(encoding="utf-8")
    assert Q8_REL in rendered
    assert Q4_REL not in rendered
    manifest = (model_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert Q8_REL in manifest
    assert Q4_REL not in manifest

    report = verify_archive(archive_root)
    assert not report.drifted
    assert report.models[0].state == "valid"
    assert report.models[0].unrecorded == []


def test_emptied_artifact_is_dropped_and_format_dir_pruned(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """Use case 2 (shed the master): hf-snapshot/* leaves no husk behind."""
    model_dir = build_model(
        archive_root,
        [entry_for(Q4_REL, Q4)],
        {Q4_REL: Q4, HF_CONFIG_REL: HF_CONFIG},
        extra_artifacts=[hf_artifact([entry_for(HF_CONFIG_REL, HF_CONFIG)])],
    )
    rm = remove_module()

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, ["hf-snapshot/*"]))

    data = json.loads((model_dir / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert [artifact["format"] for artifact in data["artifacts"]] == ["gguf"]
    assert not (model_dir / "hf-snapshot").exists()  # emptied format dir pruned
    assert (model_dir / Q4_REL).exists()


def test_record_is_updated_before_unlink_and_rerun_converges(
    archive_root: Path, build_model: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash-safety ordering (spec 0010 Notes): updated record first.

    The very first unlink raises: the record must already list only the
    survivors (extra files on disk are informational ``unrecorded``,
    never a record naming missing files). Re-running the identical
    pattern matches the leftovers as unrecorded and finishes the job.
    """
    model_dir = two_quants(build_model, archive_root)
    rm = remove_module()
    plan = rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])

    def failing(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected mid-removal fault")

    with monkeypatch.context() as mp:
        mp.setattr(os, "unlink", failing)
        mp.setattr(os, "remove", failing)
        with pytest.raises(RuntimeError, match="injected"):
            rm.execute_removal(archive_root, plan)

    assert recorded_paths(model_dir) == {Q8_REL}  # record surgery already landed
    assert (model_dir / Q4_REL).exists()  # the deletion was cut short

    replan = rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])
    by_path = {planned.path: planned for planned in replan.files}
    assert by_path[Q4_REL].unrecorded is True
    rm.execute_removal(archive_root, replan)
    assert not (model_dir / Q4_REL).exists()
    assert verify_archive(archive_root).models[0].state == "valid"


def test_symlinked_rendered_markdown_is_not_written_through(
    archive_root: Path, build_model: Callable[..., Path], tmp_path: Path
) -> None:
    """save_record writes MODEL-RECORD.md with plain write_text, which
    follows a symlink at the destination. A copied archive could plant
    it as a symlink out of tree, turning the pattern-mode rewrite into
    an arbitrary out-of-tree write (spec 0010 / the 0009 sidecar fix).
    Refuse before writing; the outside sink must survive.
    """
    model_dir = two_quants(build_model, archive_root)
    sink = tmp_path / "outside" / "config"
    sink.parent.mkdir()
    sink.write_text("original outside content")
    (model_dir / RENDERED_FILENAME).unlink(missing_ok=True)
    (model_dir / RENDERED_FILENAME).symlink_to(sink)
    rm = remove_module()

    with pytest.raises(rm.RemoveError, match="symlink"):
        rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"]))
    assert sink.read_text() == "original outside content"  # never written through


def test_symlinked_recorded_payload_is_refused_not_delisted(
    archive_root: Path, build_model: Callable[..., Path], tmp_path: Path
) -> None:
    """A symlinked recorded payload is refused (spec 0010). Silently
    skipping it would de-list the entry while the file stays on disk — a
    record/disk mismatch a re-run cannot converge (the unrecorded scan
    skips symlinks). Refuse; the record and disk stay untouched.
    """
    model_dir = two_quants(build_model, archive_root)
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"outside weight")
    (model_dir / Q4_REL).unlink()
    (model_dir / Q4_REL).symlink_to(outside)  # recorded path is now a symlink
    rm = remove_module()

    with pytest.raises(rm.RemoveError, match="symlink"):
        rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])
    assert recorded_paths(model_dir) == {Q4_REL, Q8_REL}  # record not de-listed
    assert outside.read_bytes() == b"outside weight"  # never followed


def test_pattern_removal_on_readonly_dir_writes_nothing_before_failing(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """A write fault (read-only mount) surfaces before any unlink, so the
    record is never half-updated — pattern mode writes the record first.
    """
    model_dir = two_quants(build_model, archive_root)
    rm = remove_module()
    plan = rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])
    before = recorded_paths(model_dir)
    model_dir.chmod(0o500)  # read-only dir: save_record can't write
    try:
        with pytest.raises(OSError):
            rm.execute_removal(archive_root, plan)
    finally:
        model_dir.chmod(0o700)
    assert (model_dir / Q4_REL).exists()  # nothing unlinked
    assert recorded_paths(model_dir) == before  # record untouched


def test_pattern_mode_with_missing_record_errors(
    archive_root: Path, write_model: Callable[..., Path]
) -> None:
    """Record surgery needs a readable record (spec 0010): the exit-1
    fail domain, so *not* a RemoveUserError — the exact exception type
    is the implementer's; the message must point at the record."""
    model_dir = write_model(archive_root, record=None)
    (model_dir / Q4_REL).parent.mkdir(parents=True)
    (model_dir / Q4_REL).write_bytes(Q4)
    rm = remove_module()

    with pytest.raises(Exception) as excinfo:
        rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])
    assert not isinstance(excinfo.value, rm.RemoveUserError)
    assert "record" in str(excinfo.value)


def test_pattern_mode_with_unreadable_record_errors(
    archive_root: Path, write_model: Callable[..., Path]
) -> None:
    model_dir = write_model(archive_root, record=None)
    (model_dir / RECORD_FILENAME).write_text("{ not json", encoding="utf-8")
    (model_dir / Q4_REL).parent.mkdir(parents=True)
    (model_dir / Q4_REL).write_bytes(Q4)
    rm = remove_module()

    with pytest.raises(Exception) as excinfo:
        rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])
    assert not isinstance(excinfo.value, rm.RemoveUserError)
    assert "record" in str(excinfo.value)
