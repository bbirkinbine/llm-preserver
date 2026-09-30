"""The picker withholds coverage when the layout makes the lookup unsound."""

from pathlib import Path

from migrate_shapes import Q4, Q4_REL, RENAME_TARGET_ID, build_directory, pure_rename_archive

from llm_preserver import archive_status
from llm_preserver.hub import RepoFile, RepoInfo

INFO = RepoInfo(
    commit="b" * 40,
    files=[RepoFile(Q4_REL.removeprefix("gguf/"), len(Q4), None)],
    base_model=None,
    pipeline_tag=None,
    license=None,
)


def test_unconverted_archive_marks_every_file_unavailable(tmp_path: Path) -> None:
    pure_rename_archive(tmp_path)
    naive = archive_status.snapshot_archive_status(tmp_path, RENAME_TARGET_ID, INFO)
    assert {item.state for item in naive.files.values()} == {"not-archived"}
    state = archive_status.picker_status(tmp_path, RENAME_TARGET_ID, INFO)
    assert {item.state for item in state.files.values()} == {"unavailable"}
    assert state.unavailable_reason is not None and "migrate" in state.unavailable_reason


def test_converted_archive_gets_the_per_repo_snapshot(tmp_path: Path) -> None:
    build_directory(tmp_path, RENAME_TARGET_ID, [("gguf", RENAME_TARGET_ID, {Q4_REL: Q4})])
    state = archive_status.picker_status(tmp_path, RENAME_TARGET_ID, INFO)
    assert state.unavailable_reason is None
    assert {item.state for item in state.files.values()} == {"archived"}


def test_unwalkable_models_directory_is_unavailable_not_a_traceback(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "models").symlink_to(elsewhere)
    state = archive_status.picker_status(archive, RENAME_TARGET_ID, INFO)
    assert {item.state for item in state.files.values()} == {"unavailable"}
    assert state.unavailable_reason is not None


def test_recorded_doc_leads_when_another_copy_has_a_problem() -> None:
    combined = archive_status._combine(
        [
            archive_status.FileArchiveStatus("missing"),
            archive_status.FileArchiveStatus("recorded-doc"),
        ]
    )
    assert combined == archive_status.FileArchiveStatus("recorded-doc", issues=("missing",))
