"""CLI tests for `llm-preserver verify` (spec 0009): report and exit codes.

Everything runs inside tmp_path via typer.testing.CliRunner; no real
archive and no network. The manifest sidecar, read-only guarantee, and
Ctrl-C tests live in test_cli_verify_manifest.py (300-line rule); the
CLI-free core is pinned in test_verify.py.
"""

import contextlib
import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

ENV_VAR = "LLM_PRESERVER_ARCHIVE"
PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"
EVIL = b"evil payload bytes"  # same length as PAYLOAD: only the hash differs
DOC_REL = "gguf/docs/README.md"
DOC = b"# tiny-chat docs\n"


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


def hex_of(content: bytes) -> str:
    """SHA256 hex digest of ``content``."""
    return hashlib.sha256(content).hexdigest()


def entry_for(rel_path: str, content: bytes, **overrides: object) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    made: dict[str, object] = {
        "path": rel_path,
        "sha256": hex_of(content),
        "size": len(content),
        "source": "original",
    }
    made.update(overrides)
    return made


def init_archive_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


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


def test_whole_archive_prints_one_line_per_model_and_totals(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: PAYLOAD},
        creator="beta",
        model="coder",
    )

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    out = output_of(result)
    # One result line per model, valid models included (adjudicated).
    for model_id in ("acme/tiny-chat", "beta/coder"):
        line = next(line for line in out.splitlines() if model_id in line)
        assert re.search(r"\bvalid\b", line), line
    assert "2 models" in out  # archive-wide totals summary


def test_directory_without_marker_exits_one(tmp_path: Path) -> None:
    bare = tmp_path / "notarchive"
    bare.mkdir()

    result = runner.invoke(app, ["verify", str(bare)])

    assert result.exit_code == 1
    assert "archive" in output_of(result).lower()


def test_malformed_model_option_exits_one(tmp_path: Path) -> None:
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["verify", str(archive), "--model", "noslash"])

    assert result.exit_code == 1


def test_unknown_model_option_exits_two_and_lists_archived_ids(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: PAYLOAD},
        creator="beta",
        model="coder",
    )

    result = runner.invoke(app, ["verify", str(archive), "--model", "acme/no-such-model"])

    assert result.exit_code == 2
    out = output_of(result)
    # The error self-corrects a typo: the archive's model ids are listed.
    assert "acme/tiny-chat" in out
    assert "beta/coder" in out


def test_missing_file_reports_incomplete_named_and_exits_five(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD), entry_for(DOC_REL, DOC)],
        {PAYLOAD_REL: PAYLOAD},  # DOC_REL recorded but missing on disk
    )

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    out = output_of(result)
    assert re.search(r"\bincomplete\b", out)
    assert DOC_REL in out  # each missing file is named


def test_hash_mismatch_shows_expected_and_actual_and_exits_five(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: EVIL})

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    out = output_of(result)
    assert re.search(r"\binvalid\b", out)
    assert hex_of(PAYLOAD) in out  # expected, from the record
    assert hex_of(EVIL) in out  # actual, from disk


def test_unhashed_files_report_without_tripping_exit_five(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD), entry_for(DOC_REL, DOC, sha256=None)],
        {PAYLOAD_REL: PAYLOAD, DOC_REL: DOC},
    )

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0  # a hashless record is not drift
    out = output_of(result)
    assert "unhashed" in out
    assert DOC_REL in out


def test_unrecorded_files_report_without_tripping_exit_five(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    (model_dir / "gguf" / "leftover.bin").write_bytes(b"hand-copied extra")

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0  # informational, not drift
    out = output_of(result)
    assert "unrecorded" in out
    assert "leftover.bin" in out


def test_recordless_model_reports_no_record_and_exits_five(
    tmp_path: Path, write_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    write_model(archive, record=None)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    out = output_of(result)
    assert "acme/tiny-chat" in out
    assert "no record" in out


def test_unreadable_record_reports_and_exits_five(
    tmp_path: Path, write_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = write_model(archive, record=None)
    (model_dir / "model-record.json").write_text("{ this is not json", encoding="utf-8")

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5
    out = output_of(result)
    assert "acme/tiny-chat" in out
    assert "unreadable" in out


def test_model_scope_ignores_drift_in_other_models(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})
    build_model(
        archive,
        [entry_for(PAYLOAD_REL, PAYLOAD)],
        {PAYLOAD_REL: EVIL},  # drifted — outside the scope
        creator="beta",
        model="coder",
    )

    result = runner.invoke(app, ["verify", str(archive), "--model", "acme/tiny-chat"])

    assert result.exit_code == 0
    assert "beta/coder" not in output_of(result)


def test_bare_verify_audits_the_env_var_archive(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """The cron shape: no positional path, $LLM_PRESERVER_ARCHIVE set."""
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})

    result = runner.invoke(app, ["verify"], env={ENV_VAR: str(archive)})

    assert result.exit_code == 0
    assert "acme/tiny-chat" in output_of(result)


def test_h_short_flag_shows_verify_help() -> None:
    result = runner.invoke(app, ["verify", "-h"])

    assert result.exit_code == 0
    assert "llm-preserver verify" in output_of(result)


def test_quick_states_hashes_not_checked_and_reports_complete(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, [entry_for(PAYLOAD_REL, PAYLOAD)], {PAYLOAD_REL: PAYLOAD})

    result = runner.invoke(app, ["verify", str(archive), "--quick"])

    assert result.exit_code == 0
    out = output_of(result)
    assert "hashes were not checked" in out
    assert re.search(r"\bcomplete\b", out)
    # Never "valid" from a quick run — validity requires hashing.
    assert re.search(r"\bvalid\b", out) is None
