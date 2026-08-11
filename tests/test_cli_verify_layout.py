"""CLI tests for verify's ``unmigrated`` verdict — spec 0017 pass 1.

Criterion 3: a directory whose path, ``hub_id``, and artifact
``source_repo``s disagree is reported as ``unmigrated`` — its own
verdict word, never ``valid`` and never confused with a hash failure —
and exits 1, so a scheduled verify goes red until the archive is
converted. Drift still wins the exit code (5), with ``unmigrated``
still printed on the line.

Everything runs inside tmp_path via typer.testing.CliRunner; no real
archive, no network. Output is unstyled before every substring assert
(rich emits ANSI in CI but not locally). The CLI-free core is pinned in
test_verify_layout.py.
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

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"
EVIL = b"evil payload bytes"  # same length as PAYLOAD: only the hash differs

OWN_URL = "https://huggingface.co/acme/tiny-chat"
FOREIGN_REPO = "other/tiny-chat-GGUF"
FOREIGN_URL = "https://huggingface.co/other/tiny-chat-GGUF"


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


def line_for(out: str, model_id: str) -> str:
    """The report line naming ``model_id``."""
    return next(line for line in out.splitlines() if line.startswith(model_id))


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
    """Create a model dir whose record states hub_id and source_repo.

    ``source_repo`` is a required keyword — no test here may inherit
    ``sample_record_dict``'s default (which the fixture builds in the migrated shape).
    """

    def _build(
        archive: Path,
        *,
        source_repo: str | None,
        creator: str = "acme",
        model: str = "tiny-chat",
        payload: bytes = PAYLOAD,
    ) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["source_repo"] = source_repo
        record["artifacts"][0]["files"] = [
            {
                "path": PAYLOAD_REL,
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "size": len(PAYLOAD),
                "source": "original",
            }
        ]
        model_dir = write_model(archive, record, creator=creator, model=model)
        target = model_dir / PAYLOAD_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return model_dir

    return _build


def test_unmigrated_directory_exits_one(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    # Exit 1 joins the other "your archive has a problem" outcomes, so a
    # scheduled verify goes red until the layout is converted.
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 1, output_of(result)


def test_the_layout_word_is_appended_to_the_fixity_word(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL)

    out = output_of(runner.invoke(app, ["verify", str(archive)]))

    # Appended, not substituted: the fixity word survives beside the
    # layout word, so a --quick run stays distinguishable from a
    # completed hash run (adjudicated 2026-08-11).
    assert "valid, unmigrated" in line_for(out, "acme/tiny-chat")


def test_the_line_names_the_offending_source_repo(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    # Which repo's bytes are sitting in the wrong directory is the one
    # fact the human needs to act; "unmigrated" alone does not carry it.
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL)

    out = output_of(runner.invoke(app, ["verify", str(archive)]))

    assert FOREIGN_REPO in out


def test_the_line_names_migrate_as_the_remedy(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL)

    out = output_of(runner.invoke(app, ["verify", str(archive)]))

    # Not a bare "migrate" substring: the verdict word *unmigrated*
    # contains it, so that assertion passes with the remedy line deleted
    # (mutation-proved by both reviewers, 2026-08-11).
    assert re.search(r"(?<!un)migrate", out)


def test_totals_count_the_unmigrated_model(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL)

    out = output_of(runner.invoke(app, ["verify", str(archive)]))

    assert "1 unmigrated" in out


def test_a_migrated_archive_verifies_clean_and_exits_zero(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=OWN_URL)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0, output_of(result)
    assert re.search(r"\bvalid\b", line_for(output_of(result), "acme/tiny-chat"))


def test_drift_outranks_the_layout_in_the_exit_code(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    # Both problems at once: a broken hash is damage, damage outranks
    # tidiness, so the cron contract sees spec 0009's exit 5.
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL, payload=EVIL)

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 5, output_of(result)


def test_a_drifted_model_still_prints_unmigrated_on_its_line(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL, payload=EVIL)

    out = output_of(runner.invoke(app, ["verify", str(archive)]))

    assert re.search(r"\bunmigrated\b", line_for(out, "acme/tiny-chat"))


def test_a_drifted_model_still_reports_the_hash_mismatch(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    # Winning the precedence contest must not swallow the drift detail.
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL, payload=EVIL)

    out = output_of(runner.invoke(app, ["verify", str(archive)]))

    assert hashlib.sha256(PAYLOAD).hexdigest() in out  # expected, from the record
    assert hashlib.sha256(EVIL).hexdigest() in out  # actual, from disk


def test_a_half_converted_archive_reports_each_model_honestly(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    # Mixed state is expected, not an error: migration is resumable.
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=OWN_URL)
    build_model(archive, source_repo=FOREIGN_URL, creator="beta", model="coder")

    result = runner.invoke(app, ["verify", str(archive)])
    out = output_of(result)

    assert result.exit_code == 1, out
    assert re.search(r"\bvalid\b", line_for(out, "acme/tiny-chat"))
    assert re.search(r"\bunmigrated\b", line_for(out, "beta/coder"))


def test_quick_run_reports_unmigrated_and_exits_one(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    # The verdict is hash-free, so the seconds-not-hours audit reaches
    # it too — a scheduled --quick run must not read as clean.
    archive = init_archive_dir(tmp_path)
    build_model(archive, source_repo=FOREIGN_URL)

    result = runner.invoke(app, ["verify", str(archive), "--quick"])

    assert result.exit_code == 1, output_of(result)
    assert re.search(r"\bunmigrated\b", line_for(output_of(result), "acme/tiny-chat"))
