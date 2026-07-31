"""Record-driven source scan for runtime views (spec 0002).

Decides which archived files a view may link from records alone:
payload bytes are never opened and digests are never recomputed — the
archive's recorded SHA256s are the whole basis of the seeded layout.
Every exclusion carries a reason so the CLI can report it.
"""

import re
from pathlib import Path

from pydantic import ValidationError

from llm_preserver.archive import iter_model_dirs
from llm_preserver.records import ID_COMPONENT_RE, RECORD_FILENAME, load_record
from llm_preserver.views.types import (
    ModelViewSources,
    ViewSourceFile,
    ViewSourceScan,
    ViewSourceSkip,
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SHARD_RE = re.compile(r"-\d+-of-\d+\.gguf$", re.IGNORECASE)

_NON_GGUF_REASONS = {
    "hf-snapshot": "safetensors snapshot only — no GGUF archived (Ollama runs GGUF)",
    "mlx": "mlx artifact only — no GGUF archived (Ollama runs GGUF)",
}


def scan_view_sources(archive_root: Path) -> ViewSourceScan:
    """Scan every model directory for view-linkable files.

    Args:
        archive_root: The archive root.

    Returns:
        One :class:`ModelViewSources` per model directory, in model-id
        order — eligible GGUF files with recorded digests, plus a
        reasoned skip for everything else.

    Raises:
        ArchiveError: If ``models/`` is a symlink (from the shared walk).
    """
    archive_resolved = archive_root.resolve()
    return ViewSourceScan(
        models=[
            _scan_model(model_id, model_dir, archive_resolved)
            for model_id, model_dir in iter_model_dirs(archive_root)
        ]
    )


def _scan_model(model_id: str, model_dir: Path, archive_resolved: Path) -> ModelViewSources:
    """Classify one model's recorded files as eligible or skipped."""
    result = ModelViewSources(model_id=model_id)
    creator, _, model = model_id.partition("/")
    if not (ID_COMPONENT_RE.fullmatch(creator) and ID_COMPONENT_RE.fullmatch(model)):
        # Directory names are untrusted (a foreign archive may carry
        # anything); a name that is not a valid id component must never
        # reach a minted name or a printed line (0007 posture: no hint
        # beats a booby-trapped one).
        result.skips.append(
            ViewSourceSkip(
                path=".",
                reason=(
                    "directory name is not a valid <creator>/<model> id — "
                    "refusing to mint a name from it"
                ),
            )
        )
        return result
    if not (model_dir / RECORD_FILENAME).is_file():
        result.skips.append(
            ViewSourceSkip(path=RECORD_FILENAME, reason="no record — nothing to link")
        )
        return result
    try:
        record = load_record(model_dir)
    except (ValidationError, ValueError, OSError):
        result.skips.append(
            ViewSourceSkip(
                path=RECORD_FILENAME,
                reason="record unreadable or invalid — cannot trust its digests",
            )
        )
        return result
    for artifact in record.artifacts:
        if artifact.format != "gguf":
            # One roll-up per artifact, not one line per file: a 32-file
            # snapshot must not bury the breakdown (review, 2026-07-30).
            reason = _NON_GGUF_REASONS.get(
                artifact.format, f"{artifact.format} artifact — phase 1 views link GGUF only"
            )
            count = len(artifact.files)
            noun = "file" if count == 1 else "files"
            result.skips.append(
                ViewSourceSkip(path=f"({count} {noun})", reason=reason, kind="format")
            )
            continue
        for entry in artifact.files:
            _classify_gguf_file(
                result, model_dir, archive_resolved, entry.path, entry.sha256, entry.size
            )
    return result


def _classify_gguf_file(
    result: ModelViewSources,
    model_dir: Path,
    archive_resolved: Path,
    rel_path: str,
    sha256: str | None,
    size: int | None,
) -> None:
    """Append ``rel_path`` to eligible or skips, with the reason why."""

    def skip(reason: str) -> None:
        result.skips.append(ViewSourceSkip(path=rel_path, reason=reason))

    if not rel_path.lower().endswith(".gguf"):
        result.skips.append(
            ViewSourceSkip(
                path=rel_path,
                reason="not a .gguf file — companion files are not linked in phase 1",
                kind="companion",
            )
        )
        return
    if _SHARD_RE.search(rel_path):
        skip("sharded GGUF — phase 1 does not link shard sets")
        return
    if sha256 is None:
        skip("unhashed (no recorded sha256) — a view cannot name its blob")
        return
    digest = sha256.lower()
    if not _SHA256_RE.fullmatch(digest):
        # The record schema already enforces this; the belt keeps a
        # corrupted digest from ever becoming a blob filename.
        skip("malformed sha256 in record — refusing to use it as a blob name")
        return
    abs_path = model_dir / rel_path
    if abs_path.is_symlink():
        skip("recorded file is a symlink — refusing to link through it")
        return
    if not abs_path.resolve().is_relative_to(archive_resolved):
        skip("resolves outside the archive — refusing to link it")
        return
    result.eligible.append(ViewSourceFile(path=abs_path, sha256=digest, size=size))
