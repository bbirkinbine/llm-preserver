"""Record file I/O: reading and writing ``model-record.json``.

The schema itself lives in ``records.schema``; this module owns the
on-disk concerns — the tool-owned filenames, the defensive-read caps,
and the write-record-last convention (ADR 0001).
"""

import json
from pathlib import Path

from llm_preserver.records.schema import (
    RECORD_FILENAME,
    RECORD_SCHEMA_VERSION,
    RENDERED_FILENAME,
    ModelRecord,
)

MAX_METADATA_BYTES = 1_000_000
"""Upper bound for record/marker files — far above any real record.

Metadata files are parsed even from archives the user did not author
(a copied NAS share); the cap keeps a hostile or corrupt multi-GB
"record" from exhausting memory during an inventory walk.
"""


def stamp_current_schema(record: ModelRecord) -> None:
    """Set the record's schema version to what its bytes will claim.

    Called by *both* writers, and by ``write_manifest`` **before** it
    serializes the anticipated record digest — otherwise the manifest
    commits to bytes that ``save_record`` then changes, and
    ``sha256sum -c`` fails forever on the very sidecar that exists to
    make the archive checkable without this tool (found by review,
    2026-08-11; one directory on the live archive was affected).

    ``max`` rather than assignment: a record written by a *newer* tool
    keeps its own version. ``_PreservingModel`` deliberately carries
    unknown fields through a read-modify-write, and stamping this one
    down would silence the "record schema N is newer than this tool"
    warning that tells a reader those fields exist.
    """
    record.record_schema_version = max(record.record_schema_version, RECORD_SCHEMA_VERSION)


def save_record(record: ModelRecord, model_dir: Path) -> Path:
    """Write ``model-record.json`` and its generated markdown rendering.

    Both files are written together in one call, markdown first and
    JSON last: the JSON is the source of truth, so it is the commit
    point (ADR 0001's write-record-last convention) — a failure in
    between leaves only a stale, regenerable rendering, never
    committed truth without its rendering. Nullable fields are
    serialized as explicit ``null`` values so a reader can tell
    "unknown" from "not part of this schema version".

    **The written record always claims the current schema.** Serializing
    a ``ModelRecord`` emits every field this version knows, so the bytes
    on disk *are* the current shape — a version number carried over from
    the file that was loaded would describe the old one. Found in live
    use (2026-08-11): a re-pull merged spec 0017's ``base_model`` into a
    v2 record and left it claiming v2, which is exactly backwards for a
    field whose job is telling an older tool to warn. Safe because every
    schema change so far is a widening (ADR 0001's add-never-rename
    conservatism), so an upgraded record loses nothing.

    Args:
        record: The record to persist.
        model_dir: The model directory (``models/<creator>/<model>``).

    Returns:
        The path of the written record file.

    Raises:
        OSError: If either file cannot be written.
    """
    stamp_current_schema(record)
    # Local import: render depends on this package for the model types.
    from llm_preserver.render import render_model_record

    (model_dir / RENDERED_FILENAME).write_text(render_model_record(record), encoding="utf-8")
    path = model_dir / RECORD_FILENAME
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def peek_record_schema_version(model_dir: Path) -> int | None:
    """Best-effort read of a record's claimed schema version.

    Distinguishes "corrupt record" from "record written by a newer
    tool" when full validation fails. Never raises.

    Args:
        model_dir: The model directory (``models/<creator>/<model>``).

    Returns:
        The claimed ``record_schema_version``, or None when it cannot
        be determined.
    """
    path = model_dir / RECORD_FILENAME
    try:
        if path.is_symlink() or path.stat().st_size > MAX_METADATA_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    version = data.get("record_schema_version") if isinstance(data, dict) else None
    if isinstance(version, int) and not isinstance(version, bool):
        return version
    return None


def load_record(model_dir: Path) -> ModelRecord:
    """Read and validate ``model-record.json`` from a model directory.

    Args:
        model_dir: The model directory (``models/<creator>/<model>``).

    Returns:
        The validated record.

    Raises:
        FileNotFoundError: If the directory has no record file.
        ValueError: If the record file is a symlink or implausibly
            large (see ``MAX_METADATA_BYTES``).
        pydantic.ValidationError: If the file is not valid JSON or does
            not match the schema.
    """
    path = model_dir / RECORD_FILENAME
    if path.is_symlink():
        raise ValueError(f"{path} is a symlink; refusing to read a record through it")
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError(f"{path} exceeds {MAX_METADATA_BYTES} bytes; not a plausible record")
    return ModelRecord.model_validate_json(path.read_text(encoding="utf-8"))
