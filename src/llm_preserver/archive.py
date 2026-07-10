"""Archive-root operations: init skeleton, marker, inventory walk.

Layout per ADR 0001: ``models/``, ``runtimes/``, ``manifests/`` under
an archive root identified by a versioned ``archive.json`` marker.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from llm_preserver.records import (
    MAX_METADATA_BYTES,
    RECORD_FILENAME,
    RECORD_SCHEMA_VERSION,
    ModelRecord,
    load_record,
    peek_record_schema_version,
)

SCHEMA_VERSION = 1
MARKER_FILENAME = "archive.json"
SKELETON_DIRS = ("models", "runtimes", "manifests")


class ArchiveError(Exception):
    """A directory cannot be used as (or initialized into) an archive."""


@dataclass
class ModelSummary:
    """Inventory summary for one model directory.

    Attributes:
        model_id: ``<creator>/<model>`` as laid out on disk.
        formats: Archived artifact formats, in record order.
        roles: The model's curator-assigned roles (first is primary);
            empty when no readable record exists.
        missing_record: True when the directory has no record file.
        record_error: True when the record file exists but cannot be
            parsed or validated.
        newer_record_schema: True when the record claims a
            ``record_schema_version`` newer than this tool — flagged,
            not refused, so read-only inspection stays useful.
        missing_license: True when the record carries no license.
        missing_checksums: True when any file entry lacks a SHA256.
        total_size: Sum of file sizes from record entries, in bytes.
            Never derived from the filesystem.
    """

    model_id: str
    formats: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    missing_record: bool = False
    record_error: bool = False
    newer_record_schema: bool = False
    missing_license: bool = False
    missing_checksums: bool = False
    total_size: int = 0


def _read_marker(path: Path) -> dict[str, object]:
    """Read and minimally validate an ``archive.json`` marker."""
    marker_path = path / MARKER_FILENAME
    if marker_path.is_symlink():
        raise ArchiveError(f"archive marker in {path} is a symlink; refusing to read it")
    try:
        if marker_path.stat().st_size > MAX_METADATA_BYTES:
            raise ArchiveError(f"archive marker in {path} is implausibly large; refusing")
        loaded = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"unreadable archive marker in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ArchiveError(f"archive marker in {path} is not a JSON object")
    marker: dict[str, object] = {str(key): value for key, value in loaded.items()}
    version = marker.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ArchiveError(f"archive marker in {path} has no integer schema_version")
    return marker


def is_archive(path: Path) -> bool:
    """Return True if ``path`` carries an ``archive.json`` marker."""
    return (path / MARKER_FILENAME).is_file()


def require_archive(path: Path) -> None:
    """Validate that ``path`` is an archive this tool may operate on.

    Every command — including read-only ones — calls this before
    touching archive contents (ADR 0001: tools refuse to operate on a
    newer schema than they know).

    Args:
        path: Candidate archive root.

    Raises:
        ArchiveError: If there is no valid ``archive.json`` marker, or
            its schema_version is newer than this tool understands.
    """
    if not is_archive(path):
        raise ArchiveError(f"{path} is not an llm-preserver archive (no {MARKER_FILENAME})")
    version = _read_marker(path)["schema_version"]
    if isinstance(version, int) and version > SCHEMA_VERSION:
        raise ArchiveError(
            f"archive at {path} has schema_version {version}; "
            f"this tool understands up to {SCHEMA_VERSION} — upgrade llm-preserver"
        )


def init_archive(path: Path) -> None:
    """Initialize (or idempotently revisit) an archive at ``path``.

    Creates the skeleton (``models/``, ``runtimes/``, ``manifests/``)
    and a versioned ``archive.json`` marker, writing the marker last.
    Re-running on an existing archive changes nothing. A non-empty
    directory that is not an archive is refused untouched.

    Args:
        path: Archive root; created if it does not exist.

    Raises:
        ArchiveError: If the directory is non-empty but not an archive,
            or is an archive with a schema_version newer than this tool
            understands.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        if is_archive(path):
            require_archive(path)
            for name in SKELETON_DIRS:
                (path / name).mkdir(exist_ok=True)
            return
        if any(path.iterdir()):
            raise ArchiveError(
                f"refusing to initialize: {path} is not empty and is not an existing archive"
            )
        for name in SKELETON_DIRS:
            (path / name).mkdir()
        marker_content = {"tool": "llm-preserver", "schema_version": SCHEMA_VERSION}
        marker_tmp = path / (MARKER_FILENAME + ".tmp")
        marker_tmp.write_text(json.dumps(marker_content, indent=2) + "\n", encoding="utf-8")
        marker_tmp.replace(path / MARKER_FILENAME)
    except OSError as exc:
        raise ArchiveError(f"cannot initialize archive at {path}: {exc}") from exc


def _summarize_record(model_id: str, record: ModelRecord) -> ModelSummary:
    """Build the summary for a model with a readable record."""
    files = [entry for artifact in record.artifacts for entry in artifact.files]
    return ModelSummary(
        model_id=model_id,
        formats=[artifact.format for artifact in record.artifacts],
        roles=list(record.roles),
        newer_record_schema=record.record_schema_version > RECORD_SCHEMA_VERSION,
        missing_license=record.license is None,
        missing_checksums=any(entry.sha256 is None for entry in files),
        total_size=sum(entry.size or 0 for entry in files),
    )


def _summarize_model_dir(model_dir: Path, model_id: str) -> ModelSummary:
    """Build the summary for one ``models/<creator>/<model>`` directory."""
    if not (model_dir / RECORD_FILENAME).is_file():
        return ModelSummary(model_id=model_id, missing_record=True)
    try:
        record = load_record(model_dir)
    except (ValidationError, ValueError, OSError):
        # A record from a newer tool can fail validation on values this
        # version doesn't know; say so instead of just "unreadable".
        claimed = peek_record_schema_version(model_dir)
        return ModelSummary(
            model_id=model_id,
            record_error=True,
            newer_record_schema=claimed is not None and claimed > RECORD_SCHEMA_VERSION,
        )
    return _summarize_record(model_id, record)


def inventory(root: Path) -> list[ModelSummary]:
    """Walk ``models/`` and summarize every model directory.

    Reads only record files — payload files are never stat-ed or
    hashed. Model directories without a record, or with an unparseable
    one, are reported as incomplete/error rather than raised.

    Args:
        root: The archive root.

    Returns:
        One summary per model directory, sorted by model id.

    Raises:
        ArchiveError: If ``models/`` is a symlink (same refusal as for
            symlinked model directories — it could point anywhere on
            the host).
    """
    models_root = root / "models"
    if models_root.is_symlink():
        raise ArchiveError(f"{models_root} is a symlink; refusing to walk it")
    if not models_root.is_dir():
        return []
    summaries = []
    for creator_dir in sorted(_real_subdirs(models_root)):
        for model_dir in sorted(_real_subdirs(creator_dir)):
            model_id = f"{creator_dir.name}/{model_dir.name}"
            summaries.append(_summarize_model_dir(model_dir, model_id))
    return summaries


def _real_subdirs(parent: Path) -> list[Path]:
    """List real (non-symlink) subdirectories of ``parent``.

    Symlinked "model directories" could point anywhere on the host, so
    the walk refuses to follow them.
    """
    return [p for p in parent.iterdir() if p.is_dir() and not p.is_symlink()]
