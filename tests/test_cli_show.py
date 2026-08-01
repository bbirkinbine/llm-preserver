"""Tests for the `show` command's CLI surface.

Split from test_cli.py (300-line rule). Everything runs inside
tmp_path via typer.testing.CliRunner; no real archive path and no
network are ever touched.
"""

import contextlib
import json

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

FULL_COMMIT_HASH = "a" * 40


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def test_show_refuses_newer_schema_version(tmp_path):
    archive = init_archive_dir(tmp_path)
    (archive / "archive.json").write_text(json.dumps({"schema_version": 99}))

    result = runner.invoke(app, ["show", "acme/tiny-chat", str(archive)])

    assert result.exit_code != 0


def test_show_warns_but_renders_newer_record_schema(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict(record_schema_version=99))

    result = runner.invoke(app, ["show", "acme/tiny-chat", str(archive)])

    assert result.exit_code == 0
    output = combined_output(result)
    assert "newer" in output
    assert "Q4_K_M" in output  # still renders the record


def test_show_output_has_no_file_header(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict())

    result = runner.invoke(app, ["show", "acme/tiny-chat", str(archive)])

    assert result.exit_code == 0
    assert "<!--" not in combined_output(result)


def test_show_prints_artifact_details(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict())

    result = runner.invoke(app, ["show", "acme/tiny-chat", str(archive)])

    assert result.exit_code == 0
    output = combined_output(result)
    assert "gguf" in output
    assert "Q4_K_M" in output
    assert "tiny-chat-GGUF" in output  # source repo
    assert FULL_COMMIT_HASH in output  # pinned revision
    assert "verified" in output  # provenance flag
    assert "12345" in output  # size from the record


def test_show_rejects_malformed_model_id(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["show", "noslash", str(archive)])

    assert result.exit_code != 0


def test_show_rejects_trailing_newline_in_model_id(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["show", "acme/tiny\n", str(archive)])

    assert result.exit_code != 0


def test_show_rejects_traversal_model_id(tmp_path):
    archive = init_archive_dir(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "model-record.json").write_text("{}")

    for hostile in ("../outside", "..%2Foutside", "a/../../outside", "a//etc"):
        result = runner.invoke(app, ["show", hostile, str(archive)])
        assert result.exit_code != 0, hostile


def test_show_unknown_model_exits_nonzero(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["show", "acme/no-such-model", str(archive)])

    assert result.exit_code != 0


def test_show_unparseable_record_exits_nonzero(tmp_path, write_model):
    archive = init_archive_dir(tmp_path)
    model_dir = write_model(archive, record=None)
    (model_dir / "model-record.json").write_text("{ this is not json")

    result = runner.invoke(app, ["show", "acme/tiny-chat", str(archive)])

    assert result.exit_code != 0
