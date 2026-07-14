"""Tests for llm_preserver.cli — Typer app: init, status, show.

Everything runs inside tmp_path via typer.testing.CliRunner; no real
archive path and no network are ever touched. The pull command's CLI
tests live in test_cli_pull.py.
"""

import contextlib
import json

import click
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


def test_init_exits_zero_and_creates_marker(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()

    result = runner.invoke(app, ["init", str(archive)])

    assert result.exit_code == 0
    assert (archive / "archive.json").is_file()
    assert (archive / "models").is_dir()


def test_init_rerun_on_existing_archive_exits_zero(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["init", str(archive)])

    assert result.exit_code == 0


def test_init_refuses_nonempty_non_archive_dir(tmp_path):
    target = tmp_path / "stuff"
    target.mkdir()
    (target / "somefile.txt").write_text("not an archive")

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code != 0
    assert "archive" in combined_output(result).lower()
    assert not (target / "models").exists()


def test_status_refuses_directory_without_marker(tmp_path):
    # A random directory that merely contains models/ is not an archive.
    target = tmp_path / "notarchive"
    (target / "models").mkdir(parents=True)

    result = runner.invoke(app, ["status", str(target)])

    assert result.exit_code != 0
    assert "archive" in combined_output(result).lower()


def test_status_refuses_newer_schema_version(tmp_path):
    archive = init_archive_dir(tmp_path)
    (archive / "archive.json").write_text(json.dumps({"schema_version": 99}))

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code != 0


def test_show_refuses_newer_schema_version(tmp_path):
    archive = init_archive_dir(tmp_path)
    (archive / "archive.json").write_text(json.dumps({"schema_version": 99}))

    result = runner.invoke(app, ["show", "acme/tiny-chat", str(archive)])

    assert result.exit_code != 0


def test_status_flags_newer_record_schema(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict(record_schema_version=99))

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0
    assert "newer record schema" in combined_output(result)


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


def test_status_exits_zero_on_empty_archive(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0


def test_status_lists_model_formats_and_role(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict())

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0
    output = combined_output(result)
    assert "tiny-chat" in output
    assert "gguf" in output
    assert "chat" in output


def test_status_shows_recordless_model_without_crashing(tmp_path, write_model):
    archive = init_archive_dir(tmp_path)
    write_model(archive, record=None)

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0
    assert "tiny-chat" in combined_output(result)


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


def test_init_creates_missing_directory(tmp_path):
    target = tmp_path / "new" / "nested" / "archive"

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code == 0
    assert (target / "archive.json").is_file()


def test_init_on_existing_file_exits_with_clear_error(tmp_path):
    target = tmp_path / "afile"
    target.write_text("not a directory")

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code != 0
    assert "error" in combined_output(result).lower()


def test_show_rejects_malformed_model_id(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["show", "noslash", str(archive)])

    assert result.exit_code != 0


def test_show_rejects_trailing_newline_in_model_id(tmp_path):
    archive = init_archive_dir(tmp_path)

    result = runner.invoke(app, ["show", "acme/tiny\n", str(archive)])

    assert result.exit_code != 0


def test_status_newline_in_model_dir_cannot_forge_rows(tmp_path):
    archive = init_archive_dir(tmp_path)
    weird = archive / "models" / "acme" / "x\nFAKEROW  a  b  c  ok"
    weird.mkdir(parents=True)

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0
    lines = combined_output(result).splitlines()
    assert not any(line.startswith("FAKEROW") for line in lines)


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


def test_status_does_not_write_outside_archive(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict())
    outside_before = sorted(p.name for p in tmp_path.iterdir())

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == outside_before


# The pull command's CLI tests live in test_cli_pull.py (300-line rule).


def test_status_groups_roleless_models_under_no_role_bucket(
    tmp_path, write_model, sample_record_dict
):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict(roles=[], record_schema_version=2))

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0
    assert "(no role)" in combined_output(result)


def test_h_short_flag_shows_help_at_top_level():
    result = runner.invoke(app, ["-h"])

    assert result.exit_code == 0
    assert "Usage: llm-preserver" in click.unstyle(combined_output(result))


def test_h_short_flag_works_on_every_subcommand():
    for command in ("init", "status", "show", "discover", "pull", "verify"):
        result = runner.invoke(app, [command, "-h"])

        assert result.exit_code == 0, f"{command} -h failed: {combined_output(result)}"
        assert f"llm-preserver {command}" in click.unstyle(combined_output(result))
