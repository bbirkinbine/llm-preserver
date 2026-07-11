"""Model-record schema and JSON round-trip.

The per-model ``model-record.json`` is the source of truth for
everything archived about one logical model (ADR 0001). The schema is
deliberately conservative: unknown-at-download-time fields are
explicitly nullable and serialize as ``null`` rather than being
omitted, so schema evolution is add-a-field, never rename.
"""

import datetime
import json
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RECORD_FILENAME = "model-record.json"
RENDERED_FILENAME = "MODEL-RECORD.md"
RECORD_SCHEMA_VERSION = 2
"""Schema v2 (spec 0003): per-file provenance, ``hashed-locally``
provenance state, and optional-empty ``roles``. v1 records still load —
every v2 change is a widening (new optional field, new enum value,
relaxed constraint)."""
MAX_METADATA_BYTES = 1_000_000
"""Upper bound for record/marker files — far above any real record.

Metadata files are parsed even from archives the user did not author
(a copied NAS share); the cap keeps a hostile or corrupt multi-GB
"record" from exhausting memory during an inventory walk.
"""

Role = Literal["chat", "coding", "embedding", "reranker", "multimodal"]
ArtifactFormat = Literal["gguf", "hf-snapshot", "mlx"]
Provenance = Literal["verified", "hashed-locally", "unverified"]
FileSource = Literal["original", "generated"]

_COMMIT_HASH_PATTERN = r"^[0-9a-fA-F]{40}$"
_SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"

ID_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""One segment of a ``<creator>/<model>`` id.

Hub namespaces and repo names match this; anything else (``..``,
absolute paths, extra slashes) could address files outside an
archive's ``models/`` tree and is rejected before path construction.
The single definition shared by the CLI (``show``) and pull grouping.
"""


class _PreservingModel(BaseModel):
    """Base for record models: unknown fields survive load and re-save.

    A record written by a newer tool may carry fields this version
    does not know; dropping them on a read-modify-write cycle would be
    silent data destruction, so extras are kept and serialized back
    out (``extra="allow"``).
    """

    model_config = ConfigDict(extra="allow")


class FileEntry(_PreservingModel):
    """One file belonging to an archived artifact.

    Attributes:
        path: Path relative to the model directory, upstream filename
            preserved verbatim (ADR 0001).
        sha256: Hex digest of the file, or None when not yet hashed.
        size: Size in bytes, or None when unknown.
        source: Whether the file is *original* (sacred, never
            regenerable: weights, tokenizer, license) or *generated*
            (regenerable from originals plus the record).
        provenance: How this file's hash was established (schema v2):
            ``verified`` when the local SHA256 matched a hub-declared
            hash, ``hashed-locally`` when the hub published no hash to
            check against, ``unverified`` for imports with no source
            check. None means unknown (e.g. a v1 record).
        revision: Full commit hash *this file* was downloaded (or
            adopted) at. Distinct from the artifact-level ``revision``,
            which tracks the most recent pull: per-file pins mean a
            merged artifact never implies older files were resolved at
            a newer commit. None when unknown (e.g. a v1 record).
    """

    path: str
    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    size: int | None = Field(default=None, ge=0)
    source: FileSource
    provenance: Provenance | None = None
    revision: str | None = Field(default=None, pattern=_COMMIT_HASH_PATTERN)

    @field_validator("path")
    @classmethod
    def _path_stays_in_model_dir(cls, value: str) -> str:
        """Reject paths that could escape the model directory.

        Filenames are upstream-supplied (hub repos name their own
        files), so a record must never carry an absolute path, a
        ``..`` segment, backslashes, or control characters — C0, DEL,
        and the C1 range alike (a newline could forge rows in the
        rendered provenance table; C1 covers OSC/DCS introducers).
        """
        if (
            not value
            or "\\" in value
            or any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in value)
        ):
            raise ValueError("file path must be a non-empty POSIX relative path")
        as_posix = PurePosixPath(value)
        if as_posix.is_absolute() or ".." in as_posix.parts:
            raise ValueError("file path must stay relative to the model directory (no '..')")
        return value


class ArtifactEntry(_PreservingModel):
    """One archived form of a model (a format at a quantization).

    Attributes:
        format: Weight format of this artifact.
        quantization: Quant label (e.g. ``Q4_K_M``), or None where the
            concept does not apply (full-precision snapshots).
        source_repo: URL of the hub repo the files came from — the
            *actual* source, which for third-party quants differs from
            the model's own hub id. None for imports with no known
            source.
        revision: Full commit hash of the *most recent pull* into this
            artifact. A branch name is a moving pointer, not
            provenance, and is rejected. Each file carries its own
            ``FileEntry.revision`` pin — this field says when the
            artifact was last touched, not what every file was checked
            against. None when unknown (e.g. unverified cache import).
        download_date: Date the artifact entered the archive.
        runtime_tested: Runtime/hardware the artifact was smoke-tested
            on, or None if untested.
        provenance: ``verified`` for a hub pull whose hashes match the
            pinned revision; ``unverified`` for a cache import that
            cannot be checked against a source.
        files: Every file that makes up this artifact.
    """

    format: ArtifactFormat
    quantization: str | None = None
    source_repo: str | None = None
    revision: str | None = Field(default=None, pattern=_COMMIT_HASH_PATTERN)
    download_date: datetime.date | None = None
    runtime_tested: str | None = None
    provenance: Provenance
    files: list[FileEntry] = Field(default_factory=list)


class ModelRecord(_PreservingModel):
    """The per-model record: one logical model, all archived forms.

    Attributes:
        record_schema_version: Version of the record schema itself, so
            a lone rsynced model directory stays self-describing even
            without the archive-level marker.
        name: Short human name of the model.
        hub_id: The *original* model's hub id (``creator/model``), which
            also names the model directory (ADR 0001) — even when every
            archived artifact came from third-party repos.
        roles: Curator-assigned purposes — why the model is on the
            shelf. May be empty (schema v2): roles are judgment the
            tool never fabricates, so a freshly pulled model can carry
            none yet. The first entry is the primary role and drives
            ``status`` grouping; role-less models group under a
            visible "(no role)" bucket. Distinct from ``capabilities``:
            roles are human judgment, capabilities are machine facts.
        capabilities: Machine-derived feature flags (e.g. ``tools``,
            ``vision``, ``thinking``, ``embedding``), recorded from
            source metadata by the download/import features. Free
            strings, not an enum — the vocabulary belongs to external
            reporting (hubs, runtimes) and will grow. None until
            populated.
        pipeline_tag: The source repo's Hugging Face ``pipeline_tag``,
            verbatim provenance. None until recorded.
        license: SPDX-ish license label, or None when not yet recorded.
        parameter_count: Human-readable size (e.g. ``7B``), or None.
        context_length: Context window in tokens, or None.
        notes: Free-form curator notes.
        artifacts: Every archived form of the model.
    """

    record_schema_version: int = RECORD_SCHEMA_VERSION
    name: str
    hub_id: str
    roles: list[Role] = Field(default_factory=list)
    capabilities: list[str] | None = None
    pipeline_tag: str | None = None
    license: str | None = None
    parameter_count: str | None = None
    context_length: int | None = Field(default=None, ge=0)
    notes: str | None = None
    artifacts: list[ArtifactEntry] = Field(default_factory=list)


def derive_artifact_provenance(files: Sequence[FileEntry]) -> Provenance:
    """Derive an artifact's provenance from its per-file flags.

    ``verified`` iff every file was independently verified against a
    hub-declared hash; any other file state (hashed-locally, unknown)
    demotes the artifact to ``hashed-locally``. This keeps ``verified``
    strictly honest while a hash-less README cannot pretend the weights
    were never checked — per-file flags carry the detail (spec 0003).

    Args:
        files: The artifact's file entries.

    Returns:
        The artifact-level provenance flag.
    """
    if files and all(entry.provenance == "verified" for entry in files):
        return "verified"
    return "hashed-locally"


def save_record(record: ModelRecord, model_dir: Path) -> Path:
    """Write ``model-record.json`` and its generated markdown rendering.

    Both files are written together in one call, markdown first and
    JSON last: the JSON is the source of truth, so it is the commit
    point (ADR 0001's write-record-last convention) — a failure in
    between leaves only a stale, regenerable rendering, never
    committed truth without its rendering. Nullable fields are
    serialized as explicit ``null`` values so a reader can tell
    "unknown" from "not part of this schema version".

    Args:
        record: The record to persist.
        model_dir: The model directory (``models/<creator>/<model>``).

    Returns:
        The path of the written record file.

    Raises:
        OSError: If either file cannot be written.
    """
    # Local import: render depends on this module for the model types.
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
