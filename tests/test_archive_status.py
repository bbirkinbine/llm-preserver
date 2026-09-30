"""Read-only archive observations from tiny recorded payloads (spec 0023)."""

from pathlib import Path
from typing import Any  # pytest's fixture factories expose flexible call signatures.

import pytest

from llm_preserver import archive_status
from llm_preserver.hub import RepoFile, RepoInfo

REPO = "acme/tiny-chat"
HASH = "a" * 64


def info_for(*files: RepoFile) -> RepoInfo:
    """Metadata already fetched by the caller, with no Hub client."""
    return RepoInfo(
        commit="b" * 40, files=list(files), base_model=None, pipeline_tag=None, license=None
    )


def file_entry(path: str, *, size: int | None = 3, sha256: str | None = HASH) -> dict:
    """An original payload record; its hash is evidence, never recomputed."""
    return dict(path=path, size=size, sha256=sha256, source="original")


def artifact(*entries: dict, format: str = "gguf", source: str | None = REPO) -> dict:
    """Build a source-attributed artifact in one archive layout."""
    return dict(
        format=format,
        source_repo=None if source is None else f"https://huggingface.co/{source}",
        provenance="verified",
        files=list(entries),
    )


def populate(
    tmp_path: Path,
    sample_record_dict: Any,
    write_model: Any,
    *artifacts: dict,
    payloads: dict[str, bytes] | None = None,
) -> Path:
    """Use the existing record/directory fixtures and add tiny payloads."""
    directory = write_model(tmp_path, sample_record_dict(artifacts=list(artifacts)))
    for relative, content in (payloads or {}).items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return directory


@pytest.mark.parametrize(
    ("format", "path"),
    [
        ("gguf", "gguf/Q4/part.gguf"),
        ("hf-snapshot", "hf-snapshot/Q4/part.gguf"),
        ("mlx", "mlx/Q4/part.gguf"),
    ],
)
def test_matching_record_and_present_payload_are_archived(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, format: str, path: str
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry(path), format=format),
        payloads={path: b"abc"},
    )
    snapshot = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("Q4/part.gguf", 3, HASH))
    )
    assert snapshot.files["Q4/part.gguf"].state == "archived"
    assert snapshot.files["Q4/part.gguf"].comparison == "sha256"
    assert snapshot.archive == tmp_path


@pytest.mark.parametrize(
    ("path", "expected", "comparison"),
    [
        ("gguf/docs/acme--tiny-chat/README.md", "unavailable", None),
        ("hf-snapshot/README.md", "archived", "size"),
    ],
)
def test_docs_match_both_selective_and_whole_repo_locations(
    tmp_path: Path,
    sample_record_dict: Any,
    write_model: Any,
    path: str,
    expected: str,
    comparison: str | None,
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry(path), format=path.split("/")[0]),
        payloads={path: b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("README.md", 3, None))
    )
    assert state.files["README.md"].state == expected
    assert state.files["README.md"].comparison == comparison


@pytest.mark.parametrize(("recorded", "upstream"), [(None, HASH), (HASH, None), (None, None)])
def test_missing_comparable_hashes_use_explicit_weaker_size_evidence(
    tmp_path: Path,
    sample_record_dict: Any,
    write_model: Any,
    recorded: str | None,
    upstream: str | None,
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf", sha256=recorded)),
        payloads={"gguf/a.gguf": b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, upstream))
    )
    assert state.files["a.gguf"].state == "archived"
    assert state.files["a.gguf"].comparison == "size"


def test_legacy_source_absence_matches_only_the_record_hub_id(
    tmp_path: Path, sample_record_dict: Any, write_model: Any
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf"), source=None),
        payloads={"gguf/a.gguf": b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "archived"


def test_duplicate_layouts_count_once_and_retain_missing_copy_evidence(
    tmp_path: Path, sample_record_dict: Any, write_model: Any
) -> None:
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf")),
        artifact(file_entry("hf-snapshot/a.gguf"), format="hf-snapshot"),
        payloads={"gguf/a.gguf": b"abc"},
    )
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert list(state.files) == ["a.gguf"]
    assert state.files["a.gguf"].state == "archived"
    assert "missing" in state.files["a.gguf"].issues


def test_lookup_reads_only_selected_record_and_never_opens_payloads(
    tmp_path: Path, sample_record_dict: Any, write_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(file_entry("gguf/a.gguf")),
        payloads={"gguf/a.gguf": b"abc"},
    )
    original_open = Path.open
    opened: list[Path] = []

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert path == directory / "model-record.json", f"unexpected file read: {path}"
        opened.append(path)
        return original_open(path, *args, **kwargs)

    def no_scan(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("status lookup must not scan archive directories")

    before = {p.relative_to(tmp_path): p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "iterdir", no_scan)
    monkeypatch.setattr(Path, "rglob", no_scan)
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "archived"
    assert opened == [directory / "model-record.json"]
    for relative, modified in before.items():
        assert (tmp_path / relative).stat().st_mtime_ns == modified


def test_absent_model_is_not_archived_and_lookup_creates_nothing(tmp_path: Path) -> None:
    state = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("a.gguf", 3, HASH))
    )
    assert state.files["a.gguf"].state == "not-archived"
    assert state.unavailable_reason is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("format", ["gguf", "hf-snapshot", "mlx"])
@pytest.mark.parametrize("separate_copy", [False, True])
def test_ambiguous_document_location_never_credits_two_source_files(
    tmp_path: Path,
    sample_record_dict: Any,
    write_model: Any,
    format: str,
    separate_copy: bool,
) -> None:
    nested = "docs/acme--tiny-chat/README.md"
    ambiguous = f"{format}/{nested}"
    entries = [file_entry(ambiguous)]
    payloads = {ambiguous: b"abc"}
    if separate_copy:
        entries.append(file_entry(f"{format}/README.md"))
        payloads[f"{format}/README.md"] = b"abc"
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(*entries, format=format),
        payloads=payloads,
    )
    snapshot = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile("README.md", 3, None), RepoFile(nested, 3, None))
    )
    assert snapshot.files[nested].state == "unavailable"
    root = snapshot.files["README.md"]
    assert root.state == ("archived" if separate_copy else "unavailable")
    if separate_copy:
        assert root.comparison == "size"
        assert "unavailable" in root.issues


@pytest.mark.parametrize(
    ("format", "name", "current_nested", "hash", "separate_copy"),
    [
        ("gguf", "README.md", True, HASH, False),
        ("hf-snapshot", "LICENSE", True, None, False),
        ("mlx", "README.md", True, None, False),
        ("gguf", "LICENSE", False, None, False),
        ("hf-snapshot", "README.md", False, HASH, False),
        ("mlx", "LICENSE", False, HASH, False),
        ("gguf", "README.md", False, HASH, True),
        ("mlx", "LICENSE", False, None, True),
    ],
)
def test_historical_document_alias_stays_uncertain_after_other_source_disappears(
    tmp_path: Path,
    sample_record_dict: Any,
    write_model: Any,
    format: str,
    name: str,
    current_nested: bool,
    hash: str | None,
    separate_copy: bool,
) -> None:
    nested = f"docs/acme--tiny-chat/{name}"
    target = f"{format}/{nested}"
    entries = [file_entry(target, sha256=hash)]
    payloads = {target: b"abc"}
    if separate_copy:
        direct = f"{format}/{name}"
        entries.append(file_entry(direct, sha256=hash))
        payloads[direct] = b"abc"
    populate(
        tmp_path,
        sample_record_dict,
        write_model,
        artifact(*entries, format=format),
        payloads=payloads,
    )
    current = nested if current_nested else name
    # One alias survives, but matching hashes cannot identify its historical source.
    snapshot = archive_status.snapshot_archive_status(
        tmp_path, REPO, info_for(RepoFile(current, 3, hash))
    )
    observed = snapshot.files[current]
    assert observed.state == ("archived" if separate_copy else "unavailable")
    assert observed.comparison == (("sha256" if hash else "size") if separate_copy else None)
    if separate_copy:
        assert "unavailable" in observed.issues
