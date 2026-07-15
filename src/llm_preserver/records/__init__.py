"""Per-model records: schema and JSON round-trip.

Split from a single ``records.py`` at the 300-line cap before spec
0010 touched it: ``schema`` (the Pydantic models, validation, and the
id/provenance vocabulary), ``io`` (reading and writing
``model-record.json``). This ``__init__`` re-exports the public API so
every existing ``llm_preserver.records`` import keeps working.
"""

from .io import (
    MAX_METADATA_BYTES,
    load_record,
    peek_record_schema_version,
    save_record,
)
from .schema import (
    ID_COMPONENT_RE,
    MANIFEST_FILENAME,
    RECORD_FILENAME,
    RECORD_SCHEMA_VERSION,
    RENDERED_FILENAME,
    TOOL_OWNED_ROOT_FILENAMES,
    ArtifactEntry,
    ArtifactFormat,
    FileEntry,
    FileSource,
    ModelRecord,
    Provenance,
    Role,
    derive_artifact_provenance,
)

__all__ = [
    "ID_COMPONENT_RE",
    "MANIFEST_FILENAME",
    "MAX_METADATA_BYTES",
    "RECORD_FILENAME",
    "RECORD_SCHEMA_VERSION",
    "RENDERED_FILENAME",
    "TOOL_OWNED_ROOT_FILENAMES",
    "ArtifactEntry",
    "ArtifactFormat",
    "FileEntry",
    "FileSource",
    "ModelRecord",
    "Provenance",
    "Role",
    "derive_artifact_provenance",
    "load_record",
    "peek_record_schema_version",
    "save_record",
]
