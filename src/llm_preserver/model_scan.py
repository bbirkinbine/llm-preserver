"""Shared on-disk scan of a model directory against its record.

Extracted from ``verify`` (spec 0009) when ``remove`` (spec 0010)
needed the identical unrecorded-file scan — the two commands must
never disagree about what "unrecorded" means.
"""

from pathlib import Path

from llm_preserver.records import TOOL_OWNED_ROOT_FILENAMES, ModelRecord


def unrecorded_files(model_dir: Path, record: ModelRecord) -> list[str]:
    """On-disk files no record lists, exempting tool-owned generated files.

    Args:
        model_dir: The model directory (``models/<creator>/<model>``).
        record: The model's validated record.

    Returns:
        Sorted model-dir-relative POSIX paths of regular files present
        on disk but absent from the record. Symlinks are skipped, and
        the tool-owned root files (record, rendering, manifest) are
        exempt.
    """
    recorded = {entry.path for artifact in record.artifacts for entry in artifact.files}
    found = []
    for path in model_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(model_dir).as_posix()
        if rel not in recorded and rel not in TOOL_OWNED_ROOT_FILENAMES:
            found.append(rel)
    return sorted(found)
