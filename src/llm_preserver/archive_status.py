"""Read-only archive evidence for file selection, without payload hashing.

An observation describes recorded copies across layouts. It deliberately
does not predict the later pull plan, whose destination depends on selection.
"""

import stat
from collections.abc import Mapping
from dataclasses import dataclass
from os import stat_result
from pathlib import Path
from typing import Literal, get_args

from llm_preserver.archive import ArchiveError
from llm_preserver.hub import PullUserError, RepoFile, RepoInfo
from llm_preserver.layout import model_dir_for, repo_id_from_url, unmigrated_directories
from llm_preserver.records import (
    RECORD_FILENAME,
    ArtifactFormat,
    FileEntry,
    ModelRecord,
    load_record,
)
from llm_preserver.selection import _doc_subdir_for, checked_target_path, is_doc_file

ArchiveState = Literal[
    "archived",
    "not-archived",
    "missing",
    "local-mismatch",
    "upstream-changed",
    "unavailable",
    "recorded-doc",
]
"""``recorded-doc``: a copy is on record where its source file cannot be
told apart (see ``_ambiguous_doc_targets``); docs ride along on every pull."""
Comparison = Literal["sha256", "size"]


@dataclass(frozen=True)
class FileArchiveStatus:
    """One Hub file's evidence, including problems with other recorded copies.

    Attributes:
        state: Main observation; archived means at least one matching copy.
        comparison: Metadata supporting a positive observation, never a rehash.
        issues: Additional distinct problems, including those in other layouts.
    """

    state: ArchiveState
    comparison: Comparison | None = None
    issues: tuple[ArchiveState, ...] = ()


@dataclass(frozen=True)
class ArchiveStatusSnapshot:
    """Point-in-time observations for one repo in the selected archive.

    Attributes:
        archive: The selected archive, for display.
        files: Status keyed by the original Hub file path.
        unavailable_reason: One explanation when the record cannot be inspected.
    """

    archive: Path
    files: Mapping[str, FileArchiveStatus]
    unavailable_reason: str | None = None


def _checked_stat(root: Path, target: Path) -> stat_result:
    """Inspect metadata without following symlinks below the selected root.

    The root itself may be a user-chosen alias. Every component beneath
    it must be a real directory or regular file, including the record.
    """
    current = root
    parts = target.relative_to(root).parts
    for index, part in enumerate(parts):
        current = current / part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("symlinked archive evidence")
        last = index == len(parts) - 1
        if not (stat.S_ISREG(metadata.st_mode) if last else stat.S_ISDIR(metadata.st_mode)):
            raise ValueError("archive evidence is not a regular file beneath directories")
    return metadata


def _candidate_status(
    archive: Path, model_dir: Path, entry: FileEntry, upstream: RepoFile
) -> FileArchiveStatus:
    """Compare one recorded payload with stat and already-fetched Hub metadata."""
    problems: list[ArchiveState] = []
    try:
        size = _checked_stat(archive, model_dir / entry.path).st_size
    except FileNotFoundError:
        size = None
        problems.append("missing")
    except (OSError, ValueError):
        size = None
        problems.append("unavailable")
    if size is not None:
        # Prefer the record for local drift, so an upstream size change
        # alone is not mislabeled as damage to the archived payload.
        expected = entry.size if entry.size is not None else upstream.size
        if expected is not None and size != expected:
            problems.append("local-mismatch")
    hashes_known = entry.sha256 is not None and upstream.sha256 is not None
    sizes_known = entry.size is not None and upstream.size is not None
    if (
        hashes_known
        and entry.sha256 is not None
        and upstream.sha256 is not None
        and entry.sha256.lower() != upstream.sha256.lower()
    ) or (sizes_known and entry.size != upstream.size):
        problems.append("upstream-changed")
    elif not hashes_known and not sizes_known:
        problems.append("unavailable")
    if problems:
        unique = tuple(dict.fromkeys(problems))
        return FileArchiveStatus(unique[0], issues=unique[1:])
    return FileArchiveStatus("archived", "sha256" if hashes_known else "size")


def _combine(candidates: list[FileArchiveStatus]) -> FileArchiveStatus:
    """Count a file once while retaining contradictory recorded-copy evidence."""
    if not candidates:
        return FileArchiveStatus("not-archived")
    positives = [candidate for candidate in candidates if candidate.state == "archived"]
    problems = tuple(
        dict.fromkeys(
            issue
            for candidate in candidates
            for issue in (candidate.state, *candidate.issues)
            if issue != "archived"
        )
    )
    if positives:
        comparison: Comparison = (
            "sha256" if any(item.comparison == "sha256" for item in positives) else "size"
        )
        # An unattributed doc alias says nothing against an attributed copy.
        return FileArchiveStatus(
            "archived", comparison, tuple(p for p in problems if p != "recorded-doc")
        )
    if "recorded-doc" in problems:
        problems = ("recorded-doc", *(p for p in problems if p != "recorded-doc"))
    return FileArchiveStatus(problems[0], issues=problems[1:])


def _recorded_files(record: ModelRecord | None, repo_id: str) -> dict[str, list[FileEntry]]:
    """Index original payloads only when their source attribution matches."""
    recorded: dict[str, list[FileEntry]] = {}
    if record is None:
        return recorded
    for artifact in record.artifacts:
        try:
            source = (
                record.hub_id
                if artifact.source_repo is None
                else repo_id_from_url(artifact.source_repo)
            )
        except ValueError:
            # A malformed URL is an unusable source claim, not a reason
            # to prevent selecting other files from this repo.
            continue
        if source != repo_id:
            continue
        for entry in artifact.files:
            if entry.source == "original" and entry.path.startswith(f"{artifact.format}/"):
                recorded.setdefault(entry.path, []).append(entry)
    return recorded


def _candidate_paths(repo_id: str, upstream: RepoFile) -> tuple[str, ...] | None:
    """Resolve existing layouts with the same path builder used by pull."""
    try:
        return tuple(
            dict.fromkeys(
                checked_target_path(format_name, repo_id, upstream.path, relocate_docs)
                for format_name in get_args(ArtifactFormat)
                for relocate_docs in (True, False)
            )
        )
    except PullUserError:
        return None


def _ambiguous_doc_targets(repo_id: str, recorded: Mapping[str, list[FileEntry]]) -> set[str]:
    """Find destinations whose original document path cannot be recovered.

    The selective namespace comes from the same helper as the pull path
    builder. A document beneath it could also have been archived verbatim
    in a snapshot. Current Hub names cannot disprove either historical
    origin, so these records alone must not earn positive coverage; they
    report ``recorded-doc`` instead, since every pull fetches docs anyway.
    """
    prefix = f"docs/{_doc_subdir_for(repo_id)}/"
    ambiguous = set()
    for target in recorded:
        relative = target.partition("/")[2]
        if relative.startswith(prefix) and is_doc_file(relative.removeprefix(prefix)):
            ambiguous.add(target)
    return ambiguous


def _file_status(
    archive: Path,
    model_dir: Path,
    upstream: RepoFile,
    recorded: Mapping[str, list[FileEntry]],
    paths: tuple[str, ...] | None,
    ambiguous: set[str],
) -> FileArchiveStatus:
    """Require an attributable destination before crediting a recorded copy."""
    if paths is None:
        return FileArchiveStatus("unavailable")
    return _combine(
        [
            FileArchiveStatus("recorded-doc")
            if path in ambiguous
            else _candidate_status(archive, model_dir, entry, upstream)
            for path in paths
            for entry in recorded.get(path, ())
        ]
    )


def _unavailable(archive: Path, info: RepoInfo, reason: str) -> ArchiveStatusSnapshot:
    """Withhold every observation, so no count can read as zero coverage."""
    return ArchiveStatusSnapshot(
        archive, {file.path: FileArchiveStatus("unavailable") for file in info.files}, reason
    )


def picker_status(archive: Path, repo_id: str, info: RepoInfo) -> ArchiveStatusSnapshot:
    """Observe this repo for the file picker, unless the layout makes that unsound.

    A pre-ADR-0003 archive may hold this repo's files under another
    repo's directory, where the per-repo lookup cannot see them, so its
    answer would be a confident ``not archived``. ``discover`` reaches
    the picker before pull's conversion gate refuses, so the gate's
    record-only check runs here first and withholds every observation.

    Args:
        archive: Active archive root.
        repo_id: Exact source repo selected by the human.
        info: Already-fetched Hub metadata; no additional requests are made.

    Returns:
        The repo snapshot, or one marking every file unavailable.
    """
    try:
        unconverted = bool(unmigrated_directories(archive))
    except (ArchiveError, OSError):
        return _unavailable(archive, info, "archive layout could not be read safely")
    if unconverted:
        return _unavailable(
            archive, info, "archive not yet converted to per-repo directories; run 'migrate'"
        )
    return snapshot_archive_status(archive, repo_id, info)


def snapshot_archive_status(archive: Path, repo_id: str, info: RepoInfo) -> ArchiveStatusSnapshot:
    """Observe this repo's record and payload metadata, without archive writes.

    Args:
        archive: Active archive root; no other archive is searched.
        repo_id: Exact source repo selected by the human.
        info: Already-fetched Hub metadata; no additional requests are made.

    Returns:
        A snapshot with conservative unavailable statuses on unsafe or
        unreadable evidence. Missing records mean no recorded copies.
    """
    try:
        model_dir = model_dir_for(archive, repo_id)
        try:
            _checked_stat(archive, model_dir / RECORD_FILENAME)
        except FileNotFoundError:
            record = None
        else:
            record = load_record(model_dir)
    except (OSError, ValueError):
        return _unavailable(archive, info, "archive record could not be read safely")
    recorded = _recorded_files(record, repo_id)
    ambiguous = _ambiguous_doc_targets(repo_id, recorded)
    return ArchiveStatusSnapshot(
        archive,
        {
            file.path: _file_status(
                archive, model_dir, file, recorded, _candidate_paths(repo_id, file), ambiguous
            )
            for file in info.files
        },
    )
