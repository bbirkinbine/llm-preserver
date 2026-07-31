"""Build a runtime view: validate, scan, delegate to the tool adapter.

The one public entry point over the views package. Refusals raise
:class:`ViewError` before anything is written; a zero-eligible scan
returns an empty result and writes nothing (no dest tree, no marker).
The archive root is resolved once here so every stored path, symlink
target, and containment check is absolute — a relative CLI path must
not produce dangling links (review round, 2026-07-30).
"""

from pathlib import Path

from llm_preserver.archive import require_archive
from llm_preserver.views import ollama
from llm_preserver.views.dest import refuse_bad_dest, write_marker
from llm_preserver.views.sources import scan_view_sources
from llm_preserver.views.types import ViewBuildResult, ViewError

SUPPORTED_TOOLS = ("ollama",)


def build_view(
    archive_root: Path, *, tool: str = "ollama", dest: Path, seed: bool = False
) -> ViewBuildResult:
    """Build (or refresh) a runtime view over the archive.

    Args:
        archive_root: The archive root; read-only throughout — a view
            build never writes into the archive.
        tool: Target runtime. Phase 1 supports ``ollama`` only.
        dest: View directory outside the archive; created when seeding.
        seed: When True, seed the external store (blob symlinks,
            config blobs, manifests). When False, write nothing and
            return instructions only.

    Returns:
        The build result: seeded entries, the source scan behind them,
        and next-step instructions.

    Raises:
        ViewError: Unknown tool, a dest refusal (inside the archive,
            non-empty without a valid marker, marker for a different
            archive), or a filesystem failure at the dest.
        ArchiveError: ``archive_root`` is not a usable archive.
    """
    if tool not in SUPPORTED_TOOLS:
        supported = ", ".join(SUPPORTED_TOOLS)
        raise ViewError(f"unknown view tool {tool!r} — phase 1 supports: {supported}")
    require_archive(archive_root)
    resolved = archive_root.resolve()
    previous_generated = refuse_bad_dest(resolved, dest)
    scan = scan_view_sources(resolved)
    eligible = [model for model in scan.models if model.eligible]
    if not eligible:
        return ViewBuildResult(entries=[], scan=scan, instructions="")
    if not seed:
        example_name = ollama.planned_names(eligible)[0][2]
        return ViewBuildResult(
            entries=[],
            scan=scan,
            instructions=ollama.default_instructions(resolved, dest, example_name),
        )
    try:
        dest.mkdir(parents=True, exist_ok=True)
        write_marker(resolved, dest, tool, previous_generated)
        entries, generated = ollama.seed_store(resolved, dest, eligible, previous_generated)
        write_marker(resolved, dest, tool, generated)
    except OSError as exc:
        # A read-only dest, full disk, or odd filesystem is a clean
        # refusal, never a traceback (the 0011/0012 regression class).
        raise ViewError(f"cannot write the view at {dest}: {exc}") from exc
    return ViewBuildResult(
        entries=entries, scan=scan, instructions=ollama.seed_instructions(dest, entries)
    )
