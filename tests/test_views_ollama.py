"""Ollama view adapter (spec 0002 phase 1): synthesized-manifest design.

``build_view(archive, tool="ollama", dest=Path, seed=bool)`` seeds an
external store — blob symlinks, config blobs, manifests — or returns
instructions only. Dest/marker refusals live in ``test_views_dest.py``.
"""

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.views import VIEW_MARKER_FILENAME, build_view


def hexdigest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def file_entry(rel_path: str, sha256: str | None, size: int = 0) -> dict[str, object]:
    return {"path": rel_path, "sha256": sha256, "size": size, "source": "original"}


def build(archive: Path, dest: Path, *, seed: bool):
    return build_view(archive, tool="ollama", dest=dest, seed=seed)


Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    from llm_preserver.archive import init_archive

    root = tmp_path / "archive"
    init_archive(root)
    return root


@pytest.fixture
def build_model(
    write_model: Callable[..., Path], sample_record_dict: Callable[..., dict]
) -> Callable[..., Path]:
    """Model dir with the given file entries plus 0-byte payload stand-ins."""

    def _build(
        archive: Path,
        creator: str,
        model: str,
        entries: list[dict[str, object]],
        fmt: str = "gguf",
    ) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["format"] = fmt
        if fmt != "gguf":
            record["artifacts"][0]["quantization"] = None
        record["artifacts"][0]["files"] = entries
        model_dir = write_model(archive, record, creator=creator, model=model)
        for entry in entries:
            target = model_dir / str(entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
        return model_dir

    return _build


def one_model(archive: Path, build_model: Callable[..., Path], digest: str) -> Path:
    return build_model(archive, "acme", "tiny-chat", [file_entry(Q4_REL, digest, size=7)])


def tree_state(root: Path) -> dict[Path, tuple[int, int]]:
    """Paths + (mtime_ns, size) snapshot; lstat so links are not followed."""
    return {p: (p.lstat().st_mtime_ns, p.lstat().st_size) for p in sorted(root.rglob("*"))}


def set_tree_writable(root: Path, writable: bool) -> None:
    mode_dir, mode_file = (0o755, 0o644) if writable else (0o555, 0o444)
    for p in [root, *root.rglob("*")]:
        if p.is_symlink():
            continue
        p.chmod(mode_dir if p.is_dir() else mode_file)


def test_seed_mode_builds_blob_symlink_per_eligible_gguf(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    digest = hexdigest("q4")
    model_dir = one_model(archive, build_model, digest)
    dest = tmp_path / "view"

    build(archive, dest, seed=True)

    blob = dest / "blobs" / f"sha256-{digest}"
    assert blob.is_symlink()
    assert blob.readlink().is_absolute()
    assert blob.resolve() == (model_dir / Q4_REL).resolve()


def test_relative_archive_path_still_yields_absolute_targets(
    tmp_path: Path,
    archive: Path,
    build_model: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative CLI path must not produce dangling relative symlinks."""
    digest = hexdigest("q4")
    one_model(archive, build_model, digest)
    monkeypatch.chdir(tmp_path)

    build(Path("archive"), tmp_path / "view", seed=True)

    blob = tmp_path / "view" / "blobs" / f"sha256-{digest}"
    assert blob.readlink().is_absolute()
    assert blob.resolve().exists()


def test_seed_mode_writes_manifest_and_config_blob(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    digest = hexdigest("q4")
    one_model(archive, build_model, digest)
    dest = tmp_path / "view"

    result = build(archive, dest, seed=True)

    (entry,) = result.entries
    manifest_path = dest / "manifests" / "registry.ollama.ai" / "acme" / "tiny-chat" / "q4_k_m"
    assert entry.manifest_path == manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 2
    (layer,) = manifest["layers"]
    assert layer["digest"] == f"sha256:{digest}"
    assert layer["size"] == 7  # recorded size, never stat-ed from disk
    config_digest = manifest["config"]["digest"].removeprefix("sha256:")
    config_blob = dest / "blobs" / f"sha256-{config_digest}"
    config_bytes = config_blob.read_bytes()
    assert hashlib.sha256(config_bytes).hexdigest() == config_digest  # self-consistent
    assert json.loads(config_bytes)["model_format"] == "gguf"


def test_minted_names_are_stable_across_runs_and_shaped(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    build_model(
        archive, "Acme", "Tiny-Chat", [file_entry("gguf/Tiny-Chat-Q4_K_M.gguf", hexdigest("q4"))]
    )

    first = build(archive, tmp_path / "view-a", seed=True)
    second = build(archive, tmp_path / "view-b", seed=True)

    names_first = sorted(entry.name for entry in first.entries)
    assert names_first == sorted(entry.name for entry in second.entries)
    (name,) = names_first
    assert name == "acme/tiny-chat:q4_k_m"  # deterministic minting, no judgment


def test_minted_tag_strips_model_prefix_despite_gguf_suffix(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    """The common third-party layout: dir ends -GGUF, filenames do not."""
    build_model(
        archive,
        "unsloth",
        "Tiny-Chat-GGUF",
        [file_entry("gguf/Tiny-Chat-Q4_K_M.gguf", hexdigest("q4"))],
    )

    result = build(archive, tmp_path / "view", seed=True)

    (entry,) = result.entries
    assert entry.name == "unsloth/tiny-chat-gguf:q4_k_m"


def test_default_mode_writes_nothing_and_returns_instructions(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    one_model(archive, build_model, hexdigest("q4"))
    dest = tmp_path / "view"

    result = build(archive, dest, seed=False)

    assert not dest.exists()
    for needle in ("ollama create", "OLLAMA_MODELS", "OLLAMA_NOPRUNE=1"):
        assert needle in result.instructions


def test_refresh_prunes_stale_model_and_keeps_ollama_owned_content(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    keep_digest, stale_digest = hexdigest("keep"), hexdigest("stale")
    build_model(archive, "acme", "keeper", [file_entry("gguf/keeper-Q4_K_M.gguf", keep_digest)])
    stale_dir = build_model(
        archive, "beta", "goner", [file_entry("gguf/goner-Q4_K_M.gguf", stale_digest)]
    )
    dest = tmp_path / "view"
    build(archive, dest, seed=True)
    planted = dest / "manifests" / "registry.local" / "lib" / "x" / "latest"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("ollama-owned", encoding="utf-8")
    shutil.rmtree(stale_dir)  # model removed from the archive

    build(archive, dest, seed=True)  # refresh

    assert (dest / "blobs" / f"sha256-{keep_digest}").is_symlink()
    assert not (dest / "blobs" / f"sha256-{stale_digest}").is_symlink()
    stale_manifest = dest / "manifests" / "registry.ollama.ai" / "beta" / "goner"
    assert not stale_manifest.exists()  # manifest and empty dirs pruned
    assert planted.read_text(encoding="utf-8") == "ollama-owned"  # not ours to prune


def test_prune_keeps_link_whose_target_dotdots_out_of_the_archive(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    """Containment is resolved, not lexical: `<archive>/../x` is outside."""
    one_model(archive, build_model, hexdigest("q4"))
    victim = tmp_path / "outside.txt"
    victim.write_text("keep me", encoding="utf-8")
    dest = tmp_path / "view"
    build(archive, dest, seed=True)
    sneaky = dest / "blobs" / f"sha256-{hexdigest('sneaky')}"
    sneaky.symlink_to(archive / ".." / "outside.txt")

    build(archive, dest, seed=True)  # refresh

    assert sneaky.is_symlink()  # target is really outside: not ours to prune
    assert victim.read_text(encoding="utf-8") == "keep me"


def test_generation_succeeds_against_read_only_archive(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    digest = hexdigest("q4")
    one_model(archive, build_model, digest)
    dest = tmp_path / "view"
    before = tree_state(archive)
    set_tree_writable(archive, False)
    try:
        build(archive, dest, seed=True)
    finally:
        set_tree_writable(archive, True)

    assert (dest / "blobs" / f"sha256-{digest}").is_symlink()
    assert tree_state(archive) == before  # archive untouched: paths, mtimes, sizes


def test_zero_eligible_models_creates_nothing(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    build_model(
        archive,
        "acme",
        "snapshot-only",
        [file_entry("model.safetensors", hexdigest("st"))],
        fmt="hf-snapshot",
    )
    dest = tmp_path / "view"

    result = build(archive, dest, seed=True)

    assert list(result.entries) == []
    assert not dest.exists()  # no dest tree, no marker


def test_refresh_that_shrinks_to_zero_leaves_existing_view_untouched(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    model_dir = one_model(archive, build_model, hexdigest("q4"))
    dest = tmp_path / "view"
    build(archive, dest, seed=True)
    before = tree_state(dest)
    shutil.rmtree(model_dir)

    result = build(archive, dest, seed=True)

    assert list(result.entries) == []
    assert tree_state(dest) == before  # stale view intact, nothing emptied
    assert (dest / VIEW_MARKER_FILENAME).is_file()
