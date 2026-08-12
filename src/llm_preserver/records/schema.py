"""Model-record schema: the Pydantic models and their validation.

The per-model ``model-record.json`` is the source of truth for
everything archived about one logical model (ADR 0001). The schema is
deliberately conservative: unknown-at-download-time fields are
explicitly nullable and serialize as ``null`` rather than being
omitted, so schema evolution is add-a-field, never rename. Reading
and writing the file lives in ``records.io``.
"""

import datetime
import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RECORD_SCHEMA_VERSION = 3
"""Schema v3 (spec 0017): ``base_model`` and ``base_model_source``, the
lineage the ADR 0003 layout no longer states structurally. v1 and v2
records still load — every change so far is a widening (new optional
field, new enum value, relaxed constraint)."""

RECORD_FILENAME = "model-record.json"
RENDERED_FILENAME = "MODEL-RECORD.md"
MANIFEST_FILENAME = "manifest-sha256.txt"
TOOL_OWNED_ROOT_FILENAMES = frozenset({RECORD_FILENAME, RENDERED_FILENAME, MANIFEST_FILENAME})
"""Root files the tool writes; reserved in ``FileEntry.path`` (spec
0010): a record claiming one as payload would make verify write a
manifest carrying a bogus digest line for itself, which
``sha256sum -c`` then fails forever. Only the exact root paths are
reserved — a nested file sharing the name is a different file."""

Role = Literal["chat", "coding", "embedding", "reranker", "multimodal"]
ArtifactFormat = Literal["gguf", "hf-snapshot", "mlx"]
Provenance = Literal["verified", "hashed-locally", "unverified"]
BaseModelSource = Literal["card", "asserted", "migrated"]
"""Where a recorded ``base_model`` claim came from (spec 0017).

``card`` — the source repo's model card declared it. ``asserted`` — the
curator passed ``--base-model``. ``migrated`` — harvested from the
directory a pre-ADR-0003 archive filed the artifact under, which is the
only place that judgment survived. Three origins with different
trustworthiness; a record that flattened them into one field could not
be audited later, so the origin is stored beside the claim.
"""
FileSource = Literal["original", "generated"]

_COMMIT_HASH_PATTERN = r"^[0-9a-fA-F]{40}$"
_SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"

ID_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""One segment of an ``<owner>/<repo>`` id.

Hub namespaces and repo names match this; anything else (``..``,
absolute paths, extra slashes) could address files outside an
archive's ``models/`` tree and is rejected before path construction.
The single definition shared by the CLI (``show``) and ``layout``.
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
        if value in TOOL_OWNED_ROOT_FILENAMES:
            raise ValueError(f"{value!r} is a tool-owned file, not an archivable payload path")
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
        hub_id: The **source repo's** hub id (``owner/repo``), which
            the model directory's path mirrors verbatim (ADR 0003).
            This is the *post-migration* contract: one directory holds
            one repo's bytes, so it agrees with every artifact's
            ``source_repo``. Records written before the layout change
            (and records this tool still writes until spec 0017 pass 3
            lands) carry the pre-ADR-0003 meaning — the *original*
            model's id, with third-party artifacts filed underneath.
            ``layout.layout_state`` is what tells the two apart, and
            ``verify`` reports the old shape as ``unmigrated``. The
            schema version does **not**: v3 records exist in both
            shapes.
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
        base_model: The model this repo derives from, as a hub repo id,
            or None when no claim exists. Lineage the ADR 0003 layout
            no longer states structurally — a quant repo is its own
            directory, so the relationship lives here (spec 0017).
            Never inferred: the tool records what a card declares, what
            the curator asserts, or what an old nested path meant, and
            guesses nothing.
        base_model_source: Where the ``base_model`` claim came from, so
            the three origins stay distinguishable. None whenever
            ``base_model`` is None.
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
    base_model: str | None = None
    base_model_source: BaseModelSource | None = None
    artifacts: list[ArtifactEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lineage(self) -> "ModelRecord":
        """Keep a lineage claim and its attribution inseparable.

        Two ways a record could otherwise lie about provenance, which is
        the one thing this field pair exists to prevent: an attribution
        with nothing attributed (``base_model_source: card`` and no
        ``base_model`` claims a card declared something it did not), and
        a claim that is not a usable repo id (it is rendered into
        ``MODEL-RECORD.md`` and, from pass 4, composed into commands).

        Raises:
            ValueError: If the two fields disagree, or the claim is not
                an ``<owner>/<repo>`` id.
        """
        if self.base_model is None:
            if self.base_model_source is not None:
                raise ValueError("base_model_source requires a base_model")
            return self
        owner, separator, repo = self.base_model.partition("/")
        if (
            not separator
            or not ID_COMPONENT_RE.fullmatch(owner)
            or not ID_COMPONENT_RE.fullmatch(repo)
        ):
            raise ValueError(f"base_model must look like <owner>/<repo>, got {self.base_model!r}")
        return self


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
