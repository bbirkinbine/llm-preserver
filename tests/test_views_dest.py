"""Dest and marker hardening for runtime views (spec 0002).

Regressions from the 2026-07-30 security/adversarial round: marker
existence alone must not grant write/delete rights, symlinked
tool-owned paths must never be written through (the archive-write
PoC), and degraded destinations refuse cleanly instead of crashing.
"""

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.views import VIEW_MARKER_FILENAME, ViewError, build_view


def hexdigest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def file_entry(rel_path: str, sha256: str | None, size: int = 0) -> dict[str, object]:
    return {"path": rel_path, "sha256": sha256, "size": size, "source": "original"}


def build(archive: Path, dest: Path, *, seed: bool = True):
    return build_view(archive, tool="ollama", dest=dest, seed=seed)


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
    def _build(archive: Path, creator: str, model: str, entries: list[dict[str, object]]) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["files"] = entries
        model_dir = write_model(archive, record, creator=creator, model=model)
        for entry in entries:
            target = model_dir / str(entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
        return model_dir

    return _build


@pytest.fixture
def seeded(tmp_path: Path, archive: Path, build_model: Callable[..., Path]) -> Path:
    """A valid seeded view for this archive, returned as its dest."""
    build_model(
        archive, "acme", "tiny-chat", [file_entry("gguf/tiny-chat-Q4_K_M.gguf", hexdigest("q4"))]
    )
    dest = tmp_path / "view"
    build(archive, dest)
    return dest


def test_dest_inside_archive_is_refused(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive, "acme", "tiny-chat", [file_entry("gguf/x.gguf", hexdigest("q4"))])
    dest = archive / "view"

    with pytest.raises(ViewError):
        build(archive, dest)

    assert not dest.exists()


def test_symlinked_dest_resolving_inside_archive_is_refused(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive, "acme", "tiny-chat", [file_entry("gguf/x.gguf", hexdigest("q4"))])
    inside = archive / "sneaky-dest"
    inside.mkdir()
    link = tmp_path / "outside-looking-dest"
    link.symlink_to(inside, target_is_directory=True)

    with pytest.raises(ViewError):
        build(archive, link)

    assert list(inside.iterdir()) == []


def test_non_empty_dest_without_marker_is_refused_untouched(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive, "acme", "tiny-chat", [file_entry("gguf/x.gguf", hexdigest("q4"))])
    dest = tmp_path / "not-a-view"
    dest.mkdir()
    stray = dest / "keep.txt"
    stray.write_text("mine", encoding="utf-8")

    with pytest.raises(ViewError):
        build(archive, dest)

    assert list(dest.iterdir()) == [stray]
    assert stray.read_text(encoding="utf-8") == "mine"


def test_forged_empty_marker_grants_nothing(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    """A planted `{}` marker must not authorize writes or pruning."""
    build_model(archive, "acme", "tiny-chat", [file_entry("gguf/x.gguf", hexdigest("q4"))])
    dest = tmp_path / "forged"
    dest.mkdir()
    (dest / VIEW_MARKER_FILENAME).write_text("{}", encoding="utf-8")
    stray = dest / "modelfiles"
    stray.mkdir()
    (stray / "user-data.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(ViewError):
        build(archive, dest)

    assert (stray / "user-data.txt").read_text(encoding="utf-8") == "mine"


def test_marker_for_a_different_archive_is_refused(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path], seeded: Path
) -> None:
    """Refreshing someone else's view against this archive is refused."""
    from llm_preserver.archive import init_archive

    other = tmp_path / "other-archive"
    init_archive(other)
    build_model(other, "beta", "m", [file_entry("gguf/m.gguf", hexdigest("other"))])

    with pytest.raises(ViewError, match="different archive"):
        build(other, seeded)


def test_symlinked_marker_is_refused_and_never_written_through(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path]
) -> None:
    """The archive-write PoC: marker symlinked to a record file."""
    model_dir = build_model(
        archive, "acme", "tiny-chat", [file_entry("gguf/x.gguf", hexdigest("q4"))]
    )
    record_path = model_dir / "model-record.json"
    record_before = record_path.read_bytes()
    dest = tmp_path / "trap"
    dest.mkdir()
    (dest / VIEW_MARKER_FILENAME).symlink_to(record_path)

    with pytest.raises(ViewError):
        build(archive, dest)

    assert record_path.read_bytes() == record_before  # archive intact


def test_symlinked_blobs_dir_is_refused(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path], seeded: Path
) -> None:
    """A redirected blobs/ must not receive writes elsewhere."""
    victim = tmp_path / "victim"
    victim.mkdir()
    blobs = seeded / "blobs"
    for entry in blobs.iterdir():
        entry.unlink()
    blobs.rmdir()
    blobs.symlink_to(victim, target_is_directory=True)

    with pytest.raises(ViewError):
        build(archive, seeded)

    assert list(victim.iterdir()) == []


def test_blobs_as_regular_file_is_a_clean_refusal(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path], seeded: Path
) -> None:
    blobs = seeded / "blobs"
    for entry in blobs.iterdir():
        entry.unlink()
    blobs.rmdir()
    blobs.write_text("not a dir", encoding="utf-8")

    with pytest.raises(ViewError):
        build(archive, seeded)


def test_read_only_dest_is_a_clean_refusal(
    tmp_path: Path, archive: Path, build_model: Callable[..., Path], seeded: Path
) -> None:
    """The 0011/0012 regression class: OSError at the dest, no traceback."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    seeded.chmod(0o555)
    try:
        with pytest.raises(ViewError, match="cannot write the view"):
            build(archive, seeded)
    finally:
        seeded.chmod(0o755)
