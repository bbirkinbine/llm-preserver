"""Runtime views (spec 0002): disposable per-tool trees over the archive.

A view lets an inference runtime run archived models in place —
symlinks and generated paperwork outside the archive, pointing into
it. The archive itself is never written; deleting a view loses
nothing. Phase 1 ships the shared core plus the Ollama adapter.
"""

from .build import SUPPORTED_TOOLS, build_view
from .sources import scan_view_sources
from .types import (
    VIEW_MARKER_FILENAME,
    ModelViewSources,
    ViewBuildResult,
    ViewEntry,
    ViewError,
    ViewSourceFile,
    ViewSourceScan,
    ViewSourceSkip,
)

__all__ = [
    "SUPPORTED_TOOLS",
    "VIEW_MARKER_FILENAME",
    "ModelViewSources",
    "ViewBuildResult",
    "ViewEntry",
    "ViewError",
    "ViewSourceFile",
    "ViewSourceScan",
    "ViewSourceSkip",
    "build_view",
    "scan_view_sources",
]
