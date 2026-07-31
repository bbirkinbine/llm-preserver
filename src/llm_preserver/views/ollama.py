"""Ollama view adapter (spec 0002 phase 1): synthesized manifests.

Ollama has no supported in-place mode, and its supported import
(``ollama create``) *rewrites* GGUF layers into a new full-size blob —
measured live 2026-07-30 on ollama 0.32.0, which killed the earlier
seed-and-delegate design (see the spec's gating-test record). This
adapter therefore synthesizes the store paperwork itself: one
``blobs/sha256-<digest>`` symlink per eligible GGUF (digest verbatim
from the record, never recomputed), a minimal config blob, and a
manifest per minted name. Verified live the same day: the synthesized
store lists and *serves* through the symlink with zero payload bytes
copied.

Store layout constants live in :mod:`llm_preserver.ollama_layout`
(shared with the spec 0013 match reader; provenance pinned there).
Facts specific to this adapter (external authority, fetched in-session
2026-07-30; Ollama is MIT-licensed, consulted not copied):

- Manifests use ``schemaVersion: 2`` docker media types and
  ``sha256:<hex>``-form digests. Source: github.com/ollama/ollama
  ``manifest/layer.go``, plus a real store inspected on this machine.
- ``OLLAMA_MODELS`` requires read-write; ``OLLAMA_NOPRUNE=1`` disables
  the startup prune pass that deletes manifest-unreferenced blobs.
  Sources: https://docs.ollama.com/faq, ``envconfig/config.go``.
- External stores/symlinked blobs are incidental, unsupported behavior
  (github.com/ollama/ollama/issues/1981) — hence the loud best-effort
  warning at the CLI.
"""

import hashlib
import json
import re
import shlex
from pathlib import Path

from llm_preserver.ollama_layout import (
    BLOB_PREFIX,
    BLOBS_DIRNAME,
    MANIFESTS_DIRNAME,
    MODEL_LAYER_MEDIA_TYPE,
    REGISTRY_DIRNAME,
)
from llm_preserver.views.types import ModelViewSources, ViewEntry, ViewError, ViewSourceFile

_TAG_SAFE_RE = re.compile(r"[^a-z0-9._-]")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


def seed_store(
    archive_resolved: Path,
    dest: Path,
    models: list[ModelViewSources],
    previous_generated: dict[str, object],
) -> tuple[list[ViewEntry], dict[str, object]]:
    """Seed (or refresh) the view store at ``dest``.

    Writes blob symlinks, config blobs, and manifests; prunes only what
    the tool itself wrote (tracked in the marker's ``generated`` index,
    plus archive-pointing blob symlinks). Manifests and blobs Ollama
    created in the store are never touched.

    Args:
        archive_resolved: Resolved archive root the symlinks point into.
        dest: The validated view directory (exists, marker-owned).
        models: Scan results; only eligible files are seeded.
        previous_generated: The prior run's ``generated`` index.

    Returns:
        The seeded entries and the new ``generated`` index to record
        in the marker.

    Raises:
        ViewError: If ``blobs/`` or ``manifests/`` exists as a symlink
            or non-directory (a redirected store must not receive
            writes), or a manifest path is occupied by a symlink.
    """
    blobs_dir = _require_real_dir(dest / BLOBS_DIRNAME)
    manifests_dir = _require_real_dir(dest / MANIFESTS_DIRNAME)
    _prune_previous(dest, blobs_dir, manifests_dir, previous_generated)
    entries: list[ViewEntry] = []
    for model_id, source, name in planned_names(models):
        blob_path = blobs_dir / f"{BLOB_PREFIX}{source.sha256}"
        _place_blob_link(blob_path, source.path)
        manifest_path, config_digest = _write_registration(
            blobs_dir, manifests_dir, name, source.sha256, source.size
        )
        entries.append(
            ViewEntry(
                name=name,
                model_id=model_id,
                blob_path=blob_path,
                manifest_path=manifest_path,
                config_digest=config_digest,
            )
        )
    _prune_stale_blob_links(
        blobs_dir, {entry.blob_path.name for entry in entries}, archive_resolved
    )
    generated: dict[str, object] = {
        "manifests": sorted(str(entry.manifest_path.relative_to(dest)) for entry in entries),
        "config_blobs": sorted({entry.config_digest for entry in entries}),
    }
    return entries, generated


def planned_names(models: list[ModelViewSources]) -> list[tuple[str, ViewSourceFile, str]]:
    """Mint the deterministic name for every eligible file, writing nothing.

    Shared by :func:`seed_store` and the instructions builders so a
    dry (non-seeding) run can show the exact names a seeded run will
    register.

    Returns:
        One ``(model_id, source, name)`` triple per eligible file, in
        scan order.
    """
    taken: set[str] = set()
    return [
        (model.model_id, source, _mint_name(model.model_id, source.path, source.sha256, taken))
        for model in models
        for source in model.eligible
    ]


def default_instructions(archive_root: Path, dest: Path, example_name: str) -> str:
    """The instructions-only (non-seeding) output — a complete flow.

    Every command is pasteable as printed (resume-hint philosophy):
    the exact seed invocation, the serve line, and a real ``ollama
    run`` using the first name a seeded run will register.
    """
    quoted_archive = shlex.quote(str(archive_root))
    quoted_dest = shlex.quote(str(dest))
    return (
        "Ollama cannot run models from an external folder out of the\n"
        "box — its official import copies the weights into its own\n"
        "store. Two ways to run archived models:\n"
        "\n"
        "recommended — run in place, no copy. The complete flow:\n"
        "\n"
        f"  llm-preserver views {quoted_archive} --dest {quoted_dest} --seed-store\n"
        f"  OLLAMA_MODELS={quoted_dest} OLLAMA_NOPRUNE=1 ollama serve\n"
        "  # then, from a second terminal:\n"
        "  ollama list\n"
        f"  ollama run {example_name}\n"
        "\n"
        "  or, to run alongside a normal Ollama install, put the view\n"
        "  server on its own port (hybrid setup — docs/ollama-hybrid.md):\n"
        f"  OLLAMA_MODELS={quoted_dest} OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11500 ollama serve\n"
        f"  OLLAMA_HOST=127.0.0.1:11500 ollama run {example_name}\n"
        "\n"
        "  This works — verified against the current Ollama — but is\n"
        "  not an officially supported Ollama setup, so an Ollama\n"
        "  update could break the view. Only the disposable view\n"
        "  breaks in that case (re-run --seed-store to regenerate);\n"
        "  the archive is never at risk.\n"
        "\n"
        "alternative — official import, copies the full weights into\n"
        "  Ollama's own store (~/.ollama/models by default). Use it\n"
        "  for a model you want permanently inside Ollama, or if an\n"
        "  Ollama update ever breaks views:\n"
        "  write a Modelfile containing 'FROM <archive path to a .gguf>'\n"
        "  and run: ollama create <name> -f <Modelfile>\n"
    )


def seed_instructions(dest: Path, entries: list[ViewEntry]) -> str:
    """Next steps printed after a successful seeding run."""
    quoted_dest = shlex.quote(str(dest))
    example = entries[0].name if entries else "<name>"
    lines = [
        f"seeded view store: {dest}",
        "",
        "1. serve against the seeded store (env is read at startup):",
        f"   OLLAMA_MODELS={quoted_dest} OLLAMA_NOPRUNE=1 ollama serve",
        "",
        "   or, to run alongside a normal Ollama install, put the view",
        "   server on its own port (hybrid setup — docs/ollama-hybrid.md):",
        f"   OLLAMA_MODELS={quoted_dest} OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11500 ollama serve",
        "",
        "2. from a second terminal, the models are ready — no ollama",
        "   pull, no ollama create, no network:",
        "   ollama list",
        f"   ollama run {example}",
        "",
        "   hybrid: give each client command the same port:",
        f"   OLLAMA_HOST=127.0.0.1:11500 ollama run {example}",
    ]
    return "\n".join(lines) + "\n"


def _require_real_dir(path: Path) -> Path:
    """Create ``path`` if absent; refuse a symlink or non-directory."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ViewError(f"{path} exists but is not a real directory — refusing to write through it")
    path.mkdir(exist_ok=True)
    return path


def _mint_name(model_id: str, file_path: Path, digest: str, taken: set[str]) -> str:
    """Mint a deterministic ``creator/model:tag`` name for one file.

    Purely mechanical: lowercase the archive's own creator/model
    (already validated as id components by the scan), tag with the
    filename stem — the model-name prefix stripped when present, a
    trailing ``-gguf`` on the model name tolerated — and disambiguate
    a collision with a digest prefix. No ranking, no judgment.
    """
    creator, _, model = model_id.partition("/")
    creator, model = creator.lower(), model.lower()
    stem = file_path.stem.lower()
    for prefix in (f"{model}-", f"{model.removesuffix('-gguf')}-"):
        if stem.startswith(prefix):
            stem = stem.removeprefix(prefix)
            break
    tag = _TAG_SAFE_RE.sub("-", stem) or "gguf"
    name = f"{creator}/{model}:{tag}"
    if name in taken:
        name = f"{creator}/{model}:{tag}-{digest[:8]}"
    taken.add(name)
    return name


def _place_blob_link(blob_path: Path, target: Path) -> None:
    """Point ``blob_path`` at ``target``, replacing only our own links.

    A regular file already at the blob name is content-addressed data
    with the same digest — left alone, it serves the same bytes.
    """
    if blob_path.is_symlink():
        if blob_path.readlink() == target:
            return
        blob_path.unlink()
    elif blob_path.exists():
        return
    blob_path.symlink_to(target)


def _write_registration(
    blobs_dir: Path,
    manifests_dir: Path,
    name: str,
    model_digest: str,
    size: int | None,
) -> tuple[Path, str]:
    """Write the config blob and manifest registering one model.

    Content mirrors a real ollama 0.32.0 store (inspected in-session
    2026-07-30) and was verified live: a manifest with only a model
    layer plus this minimal config lists and serves.
    """
    config_bytes = json.dumps(
        {
            "model_format": "gguf",
            "architecture": "amd64",
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [f"sha256:{model_digest}"]},
        }
    ).encode("utf-8")
    config_digest = hashlib.sha256(config_bytes).hexdigest()
    config_path = blobs_dir / f"{BLOB_PREFIX}{config_digest}"
    if not config_path.is_symlink() and not config_path.exists():
        config_path.write_bytes(config_bytes)
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {
            "mediaType": "application/vnd.docker.container.image.v1+json",
            "digest": f"sha256:{config_digest}",
            "size": len(config_bytes),
        },
        "layers": [
            {
                "mediaType": MODEL_LAYER_MEDIA_TYPE,
                "digest": f"sha256:{model_digest}",
                "size": size or 0,
            }
        ],
    }
    repo, _, tag = name.partition(":")
    manifest_path = manifests_dir / REGISTRY_DIRNAME / Path(repo) / tag
    if manifest_path.is_symlink():
        raise ViewError(f"{manifest_path} is a symlink; refusing to write through it")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path, config_digest


def _prune_previous(
    dest: Path, blobs_dir: Path, manifests_dir: Path, previous_generated: dict[str, object]
) -> None:
    """Remove content the marker's index says the tool wrote last run.

    The index is tool-written but rides in a user-readable file, so it
    is not trusted with deletion primitives: manifest entries must
    resolve under ``manifests/`` and config digests must be 64-hex
    before either names a path.
    """
    manifests = previous_generated.get("manifests")
    for rel in manifests if isinstance(manifests, list) else []:
        if not isinstance(rel, str):
            continue
        candidate = dest / rel
        if not candidate.resolve().is_relative_to(manifests_dir.resolve()):
            continue
        if candidate.is_file() and not candidate.is_symlink():
            candidate.unlink()
            _remove_empty_parents(candidate.parent, stop=manifests_dir)
    config_blobs = previous_generated.get("config_blobs")
    for digest in config_blobs if isinstance(config_blobs, list) else []:
        if not isinstance(digest, str) or not _HEX64_RE.fullmatch(digest):
            continue
        candidate = blobs_dir / f"{BLOB_PREFIX}{digest}"
        if candidate.is_file() and not candidate.is_symlink():
            candidate.unlink()


def _remove_empty_parents(directory: Path, stop: Path) -> None:
    """Remove now-empty manifest directories up to (not including) stop."""
    current = directory
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def _prune_stale_blob_links(blobs_dir: Path, wanted: set[str], archive_resolved: Path) -> None:
    """Remove archive-pointing blob symlinks no longer in the view.

    Containment is decided on the *resolved* target (a lexical prefix
    check is fooled by ``..`` segments — security round, 2026-07-30);
    only links that really point into the archive are ours to prune.
    """
    for entry in blobs_dir.iterdir():
        if not entry.is_symlink() or entry.name in wanted:
            continue
        target = entry.readlink()
        resolved = target.resolve() if target.is_absolute() else (blobs_dir / target).resolve()
        if resolved.is_relative_to(archive_resolved):
            entry.unlink()
