"""Tests for the media roles: ``diffusion``, ``video``, ``image`` (issue #69).

The curator role vocabulary (``records.schema.Role``) grows three
values for image/video generation models. Each must be accepted by
``pull --role``, recorded in the model record, shown by ``status``,
round-trip through the records package, and named by the unknown-role
error. Records carrying only the original five roles must keep loading
unchanged. Everything runs in ``tmp_path`` with the hub seam faked; no
network, no real archive.
"""

import contextlib
import json

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.records import load_record, save_record

runner = CliRunner()

MEDIA_ROLES = ("diffusion", "video", "image")
ORIGINAL_ROLES = ["chat", "coding", "embedding", "reranker", "multimodal"]
PULLED_RECORD = "models/bartowski/tiny-chat-GGUF/model-record.json"


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


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke_pull(archive, *extra_args):
    return runner.invoke(
        app,
        [
            "pull",
            "bartowski/tiny-chat-GGUF",
            str(archive),
            "--include",
            "*Q4_K_M*",
            "--yes",
            *extra_args,
        ],
    )


@pytest.mark.parametrize("role", MEDIA_ROLES)
def test_pull_role_flag_records_media_role(tmp_path, monkeypatch, fake_hub_factory, role):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--role", role)

    assert result.exit_code == 0, click.unstyle(combined_output(result))
    record = json.loads((archive / PULLED_RECORD).read_text())
    assert record["roles"] == [role]


@pytest.mark.parametrize("role", MEDIA_ROLES)
def test_status_shows_media_role_in_roles_cell(tmp_path, write_model, sample_record_dict, role):
    archive = init_archive_dir(tmp_path)
    write_model(archive, sample_record_dict(roles=[role]))

    result = runner.invoke(app, ["status", str(archive)])

    assert result.exit_code == 0
    row = next(
        line for line in click.unstyle(result.output).splitlines() if line.startswith("acme/")
    )
    model, formats, roles, *_rest = row.split()
    assert (model, formats, roles) == ("acme/tiny-chat", "gguf", role)
    assert "record unreadable" not in row


@pytest.mark.parametrize("role", MEDIA_ROLES)
def test_record_with_media_role_round_trips_through_save_and_load(
    tmp_path, write_model, sample_record_dict, role
):
    model_dir = write_model(tmp_path, sample_record_dict(roles=["chat", role]))

    save_record(load_record(model_dir), model_dir)

    assert load_record(model_dir).roles == ["chat", role]
    assert json.loads((model_dir / "model-record.json").read_text())["roles"] == ["chat", role]


def test_record_with_only_original_roles_still_loads_unchanged(
    tmp_path, write_model, sample_record_dict
):
    # Regression guard: widening the vocabulary must not disturb records
    # written before it (ADR 0001's add-never-rename conservatism).
    model_dir = write_model(tmp_path, sample_record_dict(roles=ORIGINAL_ROLES))

    assert load_record(model_dir).roles == ORIGINAL_ROLES


@pytest.mark.parametrize("role", MEDIA_ROLES)
def test_unknown_role_error_lists_media_role_as_valid(
    tmp_path, monkeypatch, fake_hub_factory, role
):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--role", "bogus")

    assert result.exit_code == 2
    output = click.unstyle(combined_output(result))
    valid_list = output.split("valid roles are", 1)[1]
    assert role in valid_list.split("\n", 1)[0]
    assert client.download_calls == []
