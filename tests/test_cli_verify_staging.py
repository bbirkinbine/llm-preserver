"""CLI tests for ``verify --staging`` (spec 0012): the deep view.

Runs inside tmp_path via typer.testing.CliRunner; no real archive, no
network. Output is unstyled (``click.unstyle``) before substring asserts
(rich-ANSI-in-CI rule). The scan primitive is pinned in
test_staging_leftovers.py; the plain-``verify`` footer lives in
test_cli_verify_footer.py.
"""

import contextlib
import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.pull_preflight import human_size
from llm_preserver.pull_prepare import STAGING_DIRNAME
from llm_preserver.records import MANIFEST_FILENAME

runner = CliRunner()

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


def entry_for(rel_path: str, content: bytes) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    return {
        "path": rel_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "source": "original",
    }


def init_archive_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def make_staging(archive: Path, creator: str, model: str, *sizes: int) -> Path:
    """Create ``.staging/<creator>/<model>/`` with one file per size."""
    leaf = archive / STAGING_DIRNAME / creator / model
    leaf.mkdir(parents=True)
    for index, size in enumerate(sizes):
        (leaf / f"part-{index}.incomplete").write_bytes(b"x" * size)
    return leaf


@pytest.fixture
def build_model(
    write_model: Callable[..., Path], sample_record_dict: Callable[..., dict]
) -> Callable[..., Path]:
    """Create a model dir with the given record entries and on-disk bytes."""

    def _build(
        archive: Path,
        entries: list[dict[str, object]],
        payloads: dict[str, bytes],
        creator: str = "acme",
        model: str = "tiny-chat",
    ) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["files"] = entries
        model_dir = write_model(archive, record, creator=creator, model=model)
        for rel_path, content in payloads.items():
            target = model_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return model_dir

    return _build


def clean_model(build_model: Callable[..., Path], archive: Path, creator: str, model: str) -> Path:
    return build_model(
        archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD}, creator, model
    )


def drift_model(build_model: Callable[..., Path], archive: Path, creator: str, model: str) -> Path:
    """A model whose disk bytes mismatch the record hash: exit-5 drift."""
    evil = b"evil payload bytes"  # same length as PAYLOAD; only the hash differs
    return build_model(
        archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: evil}, creator, model
    )


def line_with(out: str, needle: str) -> str:
    return next(line for line in out.splitlines() if needle in line)


def test_staging_lists_one_line_per_leftover_sorted_exit_zero(tmp_path: Path) -> None:
    archive = init_archive_dir(tmp_path)
    make_staging(archive, "zeta", "model", 1200, 1000)  # 2200 bytes, 2 files
    make_staging(archive, "acme", "model", 3000, 1500)  # 4500 bytes, 2 files

    result = runner.invoke(app, ["verify", str(archive), "--staging"])

    assert result.exit_code == 0
    out = output_of(result)
    acme_line = line_with(out, "acme/model")
    zeta_line = line_with(out, "zeta/model")
    assert human_size(4500) in acme_line
    assert "2 partial files" in acme_line
    assert human_size(2200) in zeta_line
    assert "2 partial files" in zeta_line
    # Sorted by id: acme before zeta.
    assert out.index("acme/model") < out.index("zeta/model")


def test_staging_on_clean_archive_reports_none_exit_zero(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    clean_model(build_model, archive, "acme", "tiny-chat")

    result = runner.invoke(app, ["verify", str(archive), "--staging"])

    assert result.exit_code == 0
    assert "no abandoned downloads in .staging/" in output_of(result)


def test_staging_never_exits_five_even_with_drift_present(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """--staging short-circuits before the audit: recorded drift is not seen."""
    archive = init_archive_dir(tmp_path)
    drift_model(build_model, archive, "acme", "tiny-chat")
    make_staging(archive, "beta", "coder", 3000, 1500)

    result = runner.invoke(app, ["verify", str(archive), "--staging"])

    assert result.exit_code == 0  # a leftover is informational, never drift
    assert "beta/coder" in output_of(result)


def test_staging_on_non_archive_dir_exits_one(tmp_path: Path) -> None:
    bare = tmp_path / "notarchive"
    bare.mkdir()

    result = runner.invoke(app, ["verify", str(bare), "--staging"])

    assert result.exit_code == 1
    assert "archive" in output_of(result).lower()


def test_staging_quick_behaves_like_staging(tmp_path: Path) -> None:
    """--quick is a documented no-op under --staging (no error)."""
    archive = init_archive_dir(tmp_path)
    make_staging(archive, "acme", "model", 3000, 1500)

    result = runner.invoke(app, ["verify", str(archive), "--staging", "--quick"])

    assert result.exit_code == 0
    out = output_of(result)
    assert "2 partial files" in line_with(out, "acme/model")


def test_staging_model_scopes_output_to_that_leftover(tmp_path: Path) -> None:
    archive = init_archive_dir(tmp_path)
    make_staging(archive, "acme", "model", 3000, 1500)
    make_staging(archive, "zeta", "model", 1200, 1000)

    result = runner.invoke(app, ["verify", str(archive), "--staging", "--model", "acme/model"])

    assert result.exit_code == 0
    out = output_of(result)
    assert "acme/model" in out
    assert "zeta/model" not in out


def test_staging_unknown_model_exits_two_and_lists_staging_ids(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """Under --staging the id namespace is staging, not models/."""
    archive = init_archive_dir(tmp_path)
    clean_model(build_model, archive, "onlymodels", "thing")  # a models/-only id
    make_staging(archive, "stage", "leftover", 3000, 1500)

    result = runner.invoke(app, ["verify", str(archive), "--staging", "--model", "zzz/nope"])

    assert result.exit_code == 2
    out = output_of(result)
    assert "stage/leftover" in out  # staging ids are listed
    assert "onlymodels/thing" not in out  # the models/ inventory is not


def test_staging_malformed_model_exits_one(tmp_path: Path) -> None:
    archive = init_archive_dir(tmp_path)
    make_staging(archive, "acme", "model", 3000, 1500)

    result = runner.invoke(app, ["verify", str(archive), "--staging", "--model", "noslash"])

    assert result.exit_code == 1


def test_staging_writes_no_manifest_and_leaves_staging_bytes(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    clean_model(build_model, archive, "acme", "tiny-chat")  # a full verify would write a sidecar
    leaf = make_staging(archive, "beta", "coder", 3000, 1500)
    before = {p: p.read_bytes() for p in sorted(leaf.iterdir())}

    result = runner.invoke(app, ["verify", str(archive), "--staging"])

    assert result.exit_code == 0
    assert list(archive.rglob(MANIFEST_FILENAME)) == []  # audit skipped: no sidecar written
    after = {p: p.read_bytes() for p in sorted(leaf.iterdir())}
    assert after == before  # staging is read-only to --staging


def test_symlinked_staging_leaf_and_creator_skipped(tmp_path: Path) -> None:
    archive = init_archive_dir(tmp_path)
    make_staging(archive, "good", "real-model", 50)
    outside = tmp_path / "outside"
    (outside / "model").mkdir(parents=True)
    (outside / "model" / "part.incomplete").write_bytes(b"y" * 70)
    (archive / STAGING_DIRNAME / "evil-creator").symlink_to(outside, target_is_directory=True)
    (archive / STAGING_DIRNAME / "good" / "evil-model").symlink_to(
        outside / "model", target_is_directory=True
    )

    result = runner.invoke(app, ["verify", str(archive), "--staging"])

    assert result.exit_code == 0
    out = output_of(result)
    assert "good/real-model" in out
    assert "evil-creator" not in out
    assert "evil-model" not in out


def test_symlinked_staging_container_exits_one(tmp_path: Path) -> None:
    archive = init_archive_dir(tmp_path)
    real = tmp_path / "real-staging"
    leaf = real / "acme" / "model"
    leaf.mkdir(parents=True)
    (leaf / "part.incomplete").write_bytes(b"x" * 10)
    (archive / STAGING_DIRNAME).symlink_to(real, target_is_directory=True)

    result = runner.invoke(app, ["verify", str(archive), "--staging"])

    assert result.exit_code == 1


def test_staging_on_unreadable_staging_exits_one(tmp_path: Path) -> None:
    """An unreadable .staging/ is a clean exit 1, never a traceback."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    archive = init_archive_dir(tmp_path)
    make_staging(archive, "acme", "model", 3000)
    staging_root = archive / STAGING_DIRNAME
    staging_root.chmod(0o000)
    try:
        result = runner.invoke(app, ["verify", str(archive), "--staging"])
    finally:
        staging_root.chmod(0o755)

    assert result.exit_code == 1
    assert not isinstance(result.exception, OSError)  # clean fail(), not a crash


def test_h_help_lists_staging_option() -> None:
    result = runner.invoke(app, ["verify", "-h"])

    assert result.exit_code == 0
    assert "--staging" in output_of(result)
