"""CLI tests for `llm-preserver remove` (spec 0010): happy paths.

Preview -> confirm -> delete, at both granularities, via
typer.testing.CliRunner in tmp archives; no network, no real archive.
Guards and fault paths live in test_cli_remove_guards.py; the CLI-free
core in test_remove.py / test_remove_patterns.py. Output is unstyled
before substring asserts (rich ANSI in CI).

Expected red (test-first): the `remove` command is not registered yet,
so every invoke exits 2 with click's no-such-command usage error.
"""

import contextlib
import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import click
import pytest
import typer.testing
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.pull_preflight import human_size
from llm_preserver.pull_prepare import STAGING_DIRNAME
from llm_preserver.records import RECORD_FILENAME

runner = CliRunner()


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an attended terminal so the confirmation prompt runs.

    Spec 0010 tightened the guard: a non-interactive run without --yes
    refuses up front, so testing the interactive confirm-and-delete path
    means forcing the runner's streams to report as a TTY (the same seam
    the progress test uses). Tests that pipe 'y'/'n' need this.
    """
    monkeypatch.setattr(typer.testing._NamedTextIOWrapper, "isatty", lambda self: True)


MODEL_ID = "acme/tiny-chat"
Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"
Q4 = b"q4 weight bytes"
Q8_REL = "gguf/tiny-chat-Q8_0.gguf"
Q8 = b"q8 weight bytes"
TOTAL_HUMAN = human_size(len(Q4) + len(Q8))


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


def two_quant_model(build_model: Callable[..., Path], archive: Path) -> Path:
    return build_model(
        archive, [entry_for(Q4_REL, Q4), entry_for(Q8_REL, Q8)], {Q4_REL: Q4, Q8_REL: Q8}
    )


def make_staging(archive: Path) -> Path:
    staging = archive / STAGING_DIRNAME / "acme" / "tiny-chat"
    staging.mkdir(parents=True)
    (staging / "tiny-chat-Q8_0.gguf.incomplete").write_bytes(b"partial transfer bytes")
    return staging


def invoke_remove(archive: Path, *extra_args: str, stdin: str | None = None):
    return runner.invoke(app, ["remove", MODEL_ID, str(archive), *extra_args], input=stdin)


def test_whole_model_confirm_deletes_model_and_staging(
    tmp_path: Path, build_model: Callable[..., Path], interactive: None
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)
    staging = make_staging(archive)

    result = invoke_remove(archive, stdin="y\n")

    assert result.exit_code == 0
    assert not model_dir.exists()
    assert not staging.exists()


def test_whole_model_preview_states_formats_counts_sizes_and_staging(
    tmp_path: Path, build_model: Callable[..., Path], interactive: None
) -> None:
    """The preview is the safety mechanism (spec 0010: no undo)."""
    archive = init_archive_dir(tmp_path)
    two_quant_model(build_model, archive)
    make_staging(archive)

    result = invoke_remove(archive, stdin="y\n")

    assert result.exit_code == 0
    out = output_of(result)
    assert "gguf" in out  # the format about to go
    assert "2 files" in out  # file count from the record
    assert TOTAL_HUMAN in out  # human sizes, same rendering as status
    assert "staging" in out.lower()  # the leftovers are disclosed too


def test_declining_the_confirmation_deletes_nothing_and_exits_zero(
    tmp_path: Path, build_model: Callable[..., Path], interactive: None
) -> None:
    """ "Nothing removed" is a successful outcome (spec 0010: an explicit
    branch, not typer.confirm(abort=True), which would exit 1)."""
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)
    staging = make_staging(archive)

    result = invoke_remove(archive, stdin="n\n")

    assert result.exit_code == 0
    assert (model_dir / RECORD_FILENAME).is_file()
    assert (model_dir / Q4_REL).read_bytes() == Q4
    assert staging.exists()


def test_yes_prints_the_full_preview_then_the_result_line(
    tmp_path: Path, build_model: Callable[..., Path]
) -> None:
    """--yes skips the question, not the disclosure (spec 0010): script
    logs must still carry the audit trail of what was deleted."""
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)

    result = invoke_remove(archive, "--yes")  # no stdin at all: must not prompt

    assert result.exit_code == 0
    assert not model_dir.exists()
    out = output_of(result)
    assert "gguf" in out
    assert "2 files" in out
    assert TOTAL_HUMAN in out
    assert re.search(r"\bremoved\b", out)  # the result line


def test_pattern_preview_lists_matches_with_sizes_and_states_what_is_kept(
    tmp_path: Path, build_model: Callable[..., Path], interactive: None
) -> None:
    archive = init_archive_dir(tmp_path)
    two_quant_model(build_model, archive)

    result = invoke_remove(archive, "--include", "*Q4_K_M*", stdin="y\n")

    assert result.exit_code == 0
    out = output_of(result)
    assert Q4_REL in out  # every matching file listed
    assert human_size(len(Q4)) in out  # with its size
    assert Q8_REL in out  # what survives is stated
    assert "kept" in out or "keep" in out


def test_pattern_confirm_deletes_only_the_matches(
    tmp_path: Path, build_model: Callable[..., Path], interactive: None
) -> None:
    archive = init_archive_dir(tmp_path)
    model_dir = two_quant_model(build_model, archive)

    result = invoke_remove(archive, "--include", "*Q4_K_M*", stdin="y\n")

    assert result.exit_code == 0
    assert not (model_dir / Q4_REL).exists()
    assert (model_dir / Q8_REL).read_bytes() == Q8
    assert (model_dir / RECORD_FILENAME).is_file()  # pattern mode never drops the record


def test_staging_only_model_offers_and_clears_the_staging_dir(
    tmp_path: Path, interactive: None
) -> None:
    """Use case 4: leftovers of an interrupted pull, no model dir."""
    archive = init_archive_dir(tmp_path)
    staging = make_staging(archive)

    result = invoke_remove(archive, stdin="y\n")

    assert result.exit_code == 0
    assert not staging.exists()
    out = output_of(result)
    assert "staging" in out.lower()
    assert re.search(r"no archived model|not archived", out, re.IGNORECASE)


def test_result_line_names_the_model(tmp_path: Path, build_model: Callable[..., Path]) -> None:
    archive = init_archive_dir(tmp_path)
    two_quant_model(build_model, archive)

    result = invoke_remove(archive, "--yes")

    assert result.exit_code == 0
    out = output_of(result)
    result_line = next(line for line in out.splitlines() if re.search(r"\bremoved\b", line))
    assert MODEL_ID in result_line
