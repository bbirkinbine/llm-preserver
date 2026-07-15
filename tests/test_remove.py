"""Core whole-model removal (spec 0010): ``llm_preserver.remove``.

The CLI-free removal core: ``plan_removal`` describes what a removal
will delete (record-derived files, staging leftovers, degraded
fallbacks) and ``execute_removal`` deletes it with the spec's
source-of-truth-first ordering (record before payload — a crash leaves
an unrecorded directory verify already surfaces, never a record naming
missing files). The module does not exist yet (test-first): imports
happen inside test bodies so collection of the rest of the suite never
depends on it — the expected red here is ModuleNotFoundError per test.

Unlike most suites, these tests write payload files inline: the disk
contents are the subject under test (``write_model`` deliberately never
creates payload). Pattern-scoped removal lives in
test_remove_patterns.py; CLI behavior in test_cli_remove*.py.
"""

import hashlib
import importlib
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.archive import init_archive
from llm_preserver.pull_prepare import STAGING_DIRNAME
from llm_preserver.records import RECORD_FILENAME

MODEL_ID = "acme/tiny-chat"
Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"
Q4 = b"q4 weight bytes"
Q8_REL = "gguf/tiny-chat-Q8_0.gguf"
Q8 = b"q8 weight bytes"


def remove_module():
    """Late import of the module under test (expected red: not yet written)."""
    return importlib.import_module("llm_preserver.remove")


def hex_of(content: bytes) -> str:
    """SHA256 hex digest of ``content``."""
    return hashlib.sha256(content).hexdigest()


def entry_for(rel_path: str, content: bytes) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    return {
        "path": rel_path,
        "sha256": hex_of(content),
        "size": len(content),
        "source": "original",
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
    """Create a model dir with the given record entries and on-disk bytes."""

    def _build(
        archive: Path,
        entries: list[dict[str, object]],
        payloads: dict[str, bytes],
        creator: str = "acme",
        model: str = "tiny-chat",
    ) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["files"] = entries
        model_dir = write_model(archive, record, creator=creator, model=model)
        for rel_path, content in payloads.items():
            target = model_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return model_dir

    return _build


def make_staging(root: Path, creator: str = "acme", model: str = "tiny-chat") -> Path:
    """Simulate interrupted-pull leftovers at the pull staging path."""
    staging = root / STAGING_DIRNAME / creator / model
    staging.mkdir(parents=True)
    (staging / "tiny-chat-Q8_0.gguf.incomplete").write_bytes(b"partial transfer bytes")
    return staging


def default_model(build_model: Callable[..., Path], root: Path) -> Path:
    """The two-quant model every test starts from."""
    return build_model(
        root,
        [entry_for(Q4_REL, Q4), entry_for(Q8_REL, Q8)],
        {Q4_REL: Q4, Q8_REL: Q8},
    )


def arm_unlink_fault(mp: pytest.MonkeyPatch, fail_after: int, exc: BaseException) -> None:
    """Make the (fail_after+1)-th unlink raise ``exc``; earlier ones succeed."""
    real_unlink = os.unlink
    calls = {"n": 0}

    def failing(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] > fail_after:
            raise exc
        real_unlink(*args, **kwargs)  # type: ignore[arg-type]

    mp.setattr(os, "unlink", failing)
    mp.setattr(os, "remove", failing)


def test_whole_model_plan_lists_recorded_files_with_record_sizes(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    default_model(build_model, archive_root)

    plan = remove_module().plan_removal(archive_root, MODEL_ID, None)

    assert plan.whole_model is True
    assert plan.record_readable is True
    assert plan.model_dir == archive_root / "models" / "acme" / "tiny-chat"
    by_path = {planned.path: planned for planned in plan.files}
    assert set(by_path) == {Q4_REL, Q8_REL}
    assert by_path[Q4_REL].size == len(Q4)
    assert by_path[Q4_REL].unrecorded is False
    assert plan.total_size == len(Q4) + len(Q8)


def test_whole_model_plan_reports_staging_dir_only_when_present(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    default_model(build_model, archive_root)
    rm = remove_module()

    assert rm.plan_removal(archive_root, MODEL_ID, None).staging_dir is None

    staging = make_staging(archive_root)
    assert rm.plan_removal(archive_root, MODEL_ID, None).staging_dir == staging


def test_execute_removes_model_dir_and_staging(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    model_dir = default_model(build_model, archive_root)
    staging = make_staging(archive_root)
    rm = remove_module()

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, None))

    assert not model_dir.exists()
    assert not staging.exists()


def test_empty_creator_dir_is_pruned_in_models_and_staging(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    default_model(build_model, archive_root)
    make_staging(archive_root)
    rm = remove_module()

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, None))

    assert not (archive_root / "models" / "acme").exists()
    assert not (archive_root / STAGING_DIRNAME / "acme").exists()


def test_other_model_of_same_creator_is_untouched(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    default_model(build_model, archive_root)
    sibling = build_model(archive_root, [entry_for(Q4_REL, Q4)], {Q4_REL: Q4}, model="other-model")
    rm = remove_module()

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, None))

    # The shared creator directory must survive with the sibling intact.
    assert (sibling / Q4_REL).read_bytes() == Q4
    assert (sibling / RECORD_FILENAME).is_file()


def test_record_is_deleted_before_payload_and_rerun_converges(
    archive_root: Path, build_model: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash-safety ordering (spec 0010 Notes): source of truth first.

    The first unlink succeeds, the second raises: after the crash the
    record must already be gone while payload survives — a state
    status/verify surface as degraded and a re-run can finish. The
    inverse ordering (record last) would leave a record naming missing
    files, which reads as corruption.
    """
    model_dir = default_model(build_model, archive_root)
    rm = remove_module()
    plan = rm.plan_removal(archive_root, MODEL_ID, None)

    with monkeypatch.context() as mp:
        arm_unlink_fault(mp, fail_after=1, exc=RuntimeError("injected mid-removal fault"))
        with pytest.raises(RuntimeError, match="injected"):
            rm.execute_removal(archive_root, plan)

    assert not (model_dir / RECORD_FILENAME).exists()  # record went first
    assert (model_dir / Q4_REL).exists() or (model_dir / Q8_REL).exists()

    # Re-run converges via the no-readable-record path (spec 0010).
    replan = rm.plan_removal(archive_root, MODEL_ID, None)
    assert replan.record_readable is False
    rm.execute_removal(archive_root, replan)
    assert not model_dir.exists()


def test_recordless_model_is_still_removable_from_disk_facts(
    archive_root: Path, write_model: Callable[..., Path]
) -> None:
    """Degraded metadata must not make a model undeletable (spec 0010)."""
    model_dir = write_model(archive_root, record=None)
    (model_dir / Q4_REL).parent.mkdir(parents=True)
    (model_dir / Q4_REL).write_bytes(Q4)
    rm = remove_module()

    plan = rm.plan_removal(archive_root, MODEL_ID, None)

    assert plan.record_readable is False
    by_path = {planned.path: planned for planned in plan.files}
    assert by_path[Q4_REL].unrecorded is True  # filesystem-derived summary
    assert by_path[Q4_REL].size == len(Q4)  # size from disk, no record to ask

    rm.execute_removal(archive_root, plan)
    assert not model_dir.exists()


def test_staging_only_model_is_removable(archive_root: Path) -> None:
    """Use case 4: interrupted pull, no model directory, only staging."""
    staging = make_staging(archive_root)
    rm = remove_module()

    plan = rm.plan_removal(archive_root, MODEL_ID, None)

    assert plan.model_dir is None
    assert plan.staging_dir == staging
    assert plan.files == []

    rm.execute_removal(archive_root, plan)
    assert not staging.exists()
    assert not (archive_root / STAGING_DIRNAME / "acme").exists()


def test_nothing_outside_the_model_and_its_staging_is_touched(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    default_model(build_model, archive_root)
    make_staging(archive_root)
    build_model(archive_root, [entry_for(Q4_REL, Q4)], {Q4_REL: Q4}, creator="beta", model="coder")
    make_staging(archive_root, creator="beta", model="coder")
    before = sorted(str(p.relative_to(archive_root)) for p in archive_root.rglob("*"))
    expected = [
        rel for rel in before if not rel.startswith(("models/acme", f"{STAGING_DIRNAME}/acme"))
    ]
    rm = remove_module()

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, None))

    after = sorted(str(p.relative_to(archive_root)) for p in archive_root.rglob("*"))
    assert after == expected


def test_recorded_path_through_symlinked_dir_never_escapes(
    archive_root: Path, write_model: Callable[..., Path], tmp_path: Path
) -> None:
    """A recorded path whose intermediate dir is a symlink out of tree
    must never be followed (spec 0010 / 0009 posture — archives may be
    copied from elsewhere). The symlink is swept, the outside data
    survives.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "tiny-chat-Q4_K_M.gguf"
    victim.write_bytes(b"precious data outside the archive")
    model_dir = write_model(archive_root, record=None)
    # planted symlink: models/acme/tiny-chat/gguf -> outside
    (model_dir / "gguf").symlink_to(outside, target_is_directory=True)
    record = {
        "name": "t",
        "hub_id": "acme/tiny-chat",
        "roles": ["chat"],
        "license": "x",
        "parameter_count": "1B",
        "context_length": 4096,
        "notes": None,
        "artifacts": [
            {
                "format": "gguf",
                "quantization": "Q4",
                "source_repo": None,
                "revision": None,
                "download_date": None,
                "runtime_tested": None,
                "provenance": "hashed-locally",
                "files": [entry_for(Q4_REL, Q4)],
            }
        ],
    }
    import json

    (model_dir / RECORD_FILENAME).write_text(json.dumps(record))
    rm = remove_module()

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, None))

    assert victim.read_bytes() == b"precious data outside the archive"  # never followed
    assert not model_dir.exists()  # the model dir (and dangling symlink) still removed


def test_symlinked_creator_dir_is_refused_before_any_deletion(
    archive_root: Path, tmp_path: Path
) -> None:
    """A leaf-only is_symlink() check misses models/<sym-creator>/<model>,
    whose rmtree would escape the archive. plan_removal must refuse the
    whole path (spec 0010 / the iter_model_dirs posture).
    """
    outside = tmp_path / "outside"
    (outside / "tiny-chat" / "gguf").mkdir(parents=True)
    victim = outside / "tiny-chat" / "gguf" / "w.gguf"
    victim.write_bytes(b"outside data reached through a symlinked creator")
    # models/acme -> outside (creator dir is the symlink, not the leaf)
    (archive_root / "models" / "acme").symlink_to(outside, target_is_directory=True)
    rm = remove_module()

    with pytest.raises(rm.RemoveError, match="symlink"):
        rm.plan_removal(archive_root, MODEL_ID, None)
    assert victim.read_bytes() == b"outside data reached through a symlinked creator"


def test_on_file_hook_fires_for_each_deleted_payload_file(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """The CLI's TTY progress hook: one callback per deleted file."""
    default_model(build_model, archive_root)
    rm = remove_module()
    seen: list[str] = []

    rm.execute_removal(archive_root, rm.plan_removal(archive_root, MODEL_ID, None), seen.append)

    assert Q4_REL in seen
    assert Q8_REL in seen
