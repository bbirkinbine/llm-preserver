"""Core pattern-scoped removal (spec 0010): what an include matches.

fnmatch globs run against the record's *archived* payload paths
(``FileEntry.path``, format-dir-prefixed — the paths ``show`` lists),
never the hub repo's filenames pull matches; on-disk unrecorded files
in non-root subtrees match too, tool-owned root files never do. The
record-surgery half (record rewrite, regeneration, crash ordering)
lives in test_remove_patterns_record.py; whole-model in
test_remove.py.

``llm_preserver.remove`` does not exist yet (test-first): imports are
lazy inside test bodies; the expected red is ModuleNotFoundError per
test. Payload files are written inline on purpose — disk contents are
the subject here.
"""

import hashlib
import importlib
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.archive import init_archive
from llm_preserver.pull_prepare import STAGING_DIRNAME
from llm_preserver.records import MANIFEST_FILENAME

MODEL_ID = "acme/tiny-chat"
Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"
Q4 = b"q4 weight bytes"
Q8_REL = "gguf/tiny-chat-Q8_0.gguf"
Q8 = b"q8 weight bytes"
DOC_REL = "gguf/docs/README.md"
DOC = b"# tiny-chat docs\n"


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
    """Create acme/tiny-chat with the given record entries and disk bytes."""

    def _build(
        archive: Path,
        entries: list[dict[str, object]],
        payloads: dict[str, bytes],
    ) -> Path:
        record = sample_record_dict()
        record["artifacts"][0]["files"] = entries
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


def test_floating_pattern_matches_the_archived_path(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """*Q4_K_M* (the 0007 resume-hint shape) matches gguf/…Q4_K_M…"""
    two_quants(build_model, archive_root)

    plan = remove_module().plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])

    assert plan.whole_model is False
    assert [planned.path for planned in plan.files] == [Q4_REL]


def test_hub_anchored_pattern_does_not_match_the_prefixed_path(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """A pattern anchored at a hub filename start misses on remove.

    Remove matches ``gguf/tiny-chat-…`` (what show lists), so
    ``tiny-chat*`` — which selects on pull — matches nothing here.
    """
    two_quants(build_model, archive_root)
    rm = remove_module()

    with pytest.raises(rm.RemoveUserError) as excinfo:
        rm.plan_removal(archive_root, MODEL_ID, ["tiny-chat*"])
    assert "tiny-chat*" in str(excinfo.value)


def test_multiple_includes_match_with_or_semantics(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(
        archive_root,
        [entry_for(Q4_REL, Q4), entry_for(Q8_REL, Q8), entry_for(DOC_REL, DOC)],
        {Q4_REL: Q4, Q8_REL: Q8, DOC_REL: DOC},
    )

    plan = remove_module().plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*", "*README*"])

    assert {planned.path for planned in plan.files} == {Q4_REL, DOC_REL}


def test_docs_neither_ride_along_nor_enjoy_protection(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """Pull's docs-ride-along rule does not carry over (spec 0010)."""
    build_model(
        archive_root,
        [entry_for(Q4_REL, Q4), entry_for(Q8_REL, Q8), entry_for(DOC_REL, DOC)],
        {Q4_REL: Q4, Q8_REL: Q8, DOC_REL: DOC},
    )
    rm = remove_module()

    quant_plan = rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])
    assert {planned.path for planned in quant_plan.files} == {Q4_REL}  # doc not dragged in

    doc_plan = rm.plan_removal(archive_root, MODEL_ID, ["*README*"])
    assert {planned.path for planned in doc_plan.files} == {DOC_REL}  # doc not protected


def test_unrecorded_on_disk_files_match_and_are_flagged(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """Stranded junk a pattern names is removable without whole-model."""
    model_dir = two_quants(build_model, archive_root)
    junk = model_dir / "gguf" / "stray-Q4_K_M.tmp"
    junk.write_bytes(b"stranded junk bytes")

    plan = remove_module().plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])

    by_path = {planned.path: planned for planned in plan.files}
    assert set(by_path) == {Q4_REL, "gguf/stray-Q4_K_M.tmp"}
    assert by_path["gguf/stray-Q4_K_M.tmp"].unrecorded is True
    assert by_path["gguf/stray-Q4_K_M.tmp"].size == len(b"stranded junk bytes")  # from disk
    assert by_path[Q4_REL].unrecorded is False


def test_root_level_stray_is_not_pattern_removable(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """Root-level strays stay unmatched (spec 0010): a file at the model
    dir root sits where the tool-owned files live and is not archived
    payload, so a pattern must not delete it — only non-root subtree
    strays are pattern-removable.
    """
    model_dir = two_quants(build_model, archive_root)
    stray = model_dir / "IMPORTANT-NOTES.txt"
    stray.write_bytes(b"hand-placed root note")
    rm = remove_module()

    # A pattern that would match the root file matches nothing archivable.
    with pytest.raises(rm.RemoveUserError):
        rm.plan_removal(archive_root, MODEL_ID, ["*.txt"])
    assert stray.read_bytes() == b"hand-placed root note"


def test_pattern_matching_every_payload_file_is_refused(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """Pattern removal never empties the model silently (spec 0010)."""
    model_dir = two_quants(build_model, archive_root)
    rm = remove_module()

    with pytest.raises(rm.RemoveUserError) as excinfo:
        rm.plan_removal(archive_root, MODEL_ID, ["*"])
    # The refusal points at plain remove, the sanctioned whole-model path.
    assert "remove" in str(excinfo.value)
    assert (model_dir / Q4_REL).exists()


def test_tool_owned_root_files_never_match_a_pattern(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """manifest-sha256.txt is managed, not a removable target."""
    model_dir = two_quants(build_model, archive_root)
    (model_dir / MANIFEST_FILENAME).write_text("stale manifest\n", encoding="utf-8")
    rm = remove_module()

    with pytest.raises(rm.RemoveUserError) as excinfo:
        rm.plan_removal(archive_root, MODEL_ID, ["manifest*"])
    assert "manifest*" in str(excinfo.value)  # echoed: a no-op delete is user error
    assert (model_dir / MANIFEST_FILENAME).exists()


def test_pattern_removal_never_touches_staging(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    """Staging belongs to in-flight pulls; whole-model owns the sweep."""
    two_quants(build_model, archive_root)
    staging = archive_root / STAGING_DIRNAME / "acme" / "tiny-chat"
    staging.mkdir(parents=True)
    leftover = staging / "tiny-chat-Q4_K_M.gguf.incomplete"
    leftover.write_bytes(b"partial transfer bytes")
    rm = remove_module()

    plan = rm.plan_removal(archive_root, MODEL_ID, ["*Q4_K_M*"])
    assert plan.staging_dir is None
    rm.execute_removal(archive_root, plan)

    assert leftover.read_bytes() == b"partial transfer bytes"
