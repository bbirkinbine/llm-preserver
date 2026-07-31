"""View destination preflight and the generated-tree marker (spec 0002).

The invariants every adapter inherits: a view is never written inside
the archive (the archive stays payload-immutable; ADR 0001), and the
tool only writes into a destination it owns — a fresh/empty directory,
or one carrying a *valid* marker naming this same archive. Marker
existence alone is not trusted (a planted empty file must not grant
delete/prune rights — security round, 2026-07-30); the content is
validated and the recorded archive root must match.
"""

import json
import os
import tempfile
from pathlib import Path

from llm_preserver.records import MAX_METADATA_BYTES
from llm_preserver.views.types import VIEW_MARKER_FILENAME, ViewError


def refuse_bad_dest(archive_resolved: Path, dest: Path) -> dict[str, object]:
    """Validate a view destination without writing anything.

    Args:
        archive_resolved: Resolved archive root the view will point into.
        dest: The requested view directory (may not exist yet).

    Returns:
        The prior run's ``generated`` index (what the tool wrote last
        time, for pruning), or an empty dict on a fresh destination.

    Raises:
        ViewError: If ``dest`` resolves inside the archive (symlinked
            paths included), is not a directory, is non-empty without a
            valid marker, or carries a marker for a different archive.
    """
    if dest.resolve().is_relative_to(archive_resolved):
        raise ViewError(
            f"dest {dest} is inside the archive at {archive_resolved} — "
            "views are disposable and never live in the archive"
        )
    if not dest.exists():
        return {}
    if not dest.is_dir():
        raise ViewError(f"dest {dest} exists and is not a directory")
    marker = _load_marker(dest, archive_resolved)
    if marker is None:
        if any(dest.iterdir()):
            raise ViewError(
                f"dest {dest} is not empty and has no valid {VIEW_MARKER_FILENAME} — "
                "refusing to write into a directory this tool does not own"
            )
        return {}
    generated = marker.get("generated")
    return generated if isinstance(generated, dict) else {}


def write_marker(
    archive_resolved: Path, dest: Path, tool: str, generated: dict[str, object]
) -> None:
    """Write the view marker, symlink-safely.

    ``mkstemp`` + ``os.replace`` so a planted symlink at the marker
    name is replaced, never written *through* (the 0010
    ``MODEL-RECORD.md`` lesson — a symlink into the archive must not
    receive the marker bytes).

    Args:
        archive_resolved: Resolved archive root, recorded for the
            next run's ownership check.
        dest: The view directory (must exist).
        tool: The runtime the view targets (e.g. ``ollama``).
        generated: Index of tool-written content (manifest relpaths,
            config-blob digests) the next refresh may prune.
    """
    content = {
        "tool": "llm-preserver",
        "kind": "runtime-view",
        "view_tool": tool,
        "archive_root": str(archive_resolved),
        "note": (
            "generated and disposable — deleting this tree loses "
            "nothing; the archive is the source of truth"
        ),
        "generated": generated,
    }
    fd, tmp_name = tempfile.mkstemp(dir=dest, prefix=".view-marker-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(content, indent=2) + "\n")
        Path(tmp_name).replace(dest / VIEW_MARKER_FILENAME)
    except OSError:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _load_marker(dest: Path, archive_resolved: Path) -> dict[str, object] | None:
    """Read and validate the marker; None when there is none at all."""
    marker_path = dest / VIEW_MARKER_FILENAME
    if marker_path.is_symlink():
        raise ViewError(f"{marker_path} is a symlink; refusing to treat it as a view marker")
    if not marker_path.is_file():
        return None
    try:
        if marker_path.stat().st_size > MAX_METADATA_BYTES:
            raise ViewError(f"{marker_path} is implausibly large; refusing")
        loaded = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ViewError(f"unreadable view marker {marker_path}: {exc}") from exc
    if (
        not isinstance(loaded, dict)
        or loaded.get("tool") != "llm-preserver"
        or loaded.get("kind") != "runtime-view"
    ):
        raise ViewError(
            f"{marker_path} is not a marker this tool wrote — "
            "refusing to treat the directory as a view"
        )
    if loaded.get("archive_root") != str(archive_resolved):
        raise ViewError(
            f"dest {dest} is a view of a different archive "
            f"({loaded.get('archive_root')!r}) — refusing to refresh it "
            f"against {archive_resolved}"
        )
    return {str(key): value for key, value in loaded.items()}
