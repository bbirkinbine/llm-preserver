"""``pull --base-model`` — spec 0017 pass 4, criteria 5 and 6.

ADR 0003 deletes ``--model``, which was doing two jobs: choosing the
directory *and* asserting "this repo is a conversion of that model".
Only the first job should disappear. ``--base-model`` keeps the second
one, recorded and attributed, and never consulted for the destination.

The attribution is the point. Three sources can supply a lineage claim
(a card, a curator, a migration), they are not equally trustworthy, and
a record that flattened them into one field could not be audited later.
"""

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.records import load_record

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
DECLARED_BASE = "acme/tiny-chat"  # what FakeHubClient's card declares
ASSERTED_BASE = "meta-models/some-other-model"


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def model_dir(archive):
    return archive / "models" / "bartowski" / "tiny-chat-GGUF"


def pull(archive, monkeypatch, fake_hub_factory, *extra):
    """Run a selective pull through the CLI with the fake hub seam."""
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: fake_hub_factory())
    return runner.invoke(
        app,
        ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", "--yes", *extra],
    )


def test_a_pull_records_the_lineage_the_card_declares(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)

    result = pull(archive, monkeypatch, fake_hub_factory)

    assert result.exit_code == 0, click.unstyle(result.output)
    record = load_record(model_dir(archive))
    assert (record.base_model, record.base_model_source) == (DECLARED_BASE, "card")


def test_base_model_flag_overrides_what_the_card_declares(tmp_path, monkeypatch, fake_hub_factory):
    # A human looking at the repo outranks metadata they can see is
    # wrong — and the record says which of the two it is.
    archive = init_archive_dir(tmp_path)

    result = pull(archive, monkeypatch, fake_hub_factory, "--base-model", ASSERTED_BASE)

    assert result.exit_code == 0, click.unstyle(result.output)
    record = load_record(model_dir(archive))
    assert (record.base_model, record.base_model_source) == (ASSERTED_BASE, "asserted")


def test_base_model_never_changes_where_the_files_land(tmp_path, monkeypatch, fake_hub_factory):
    # The whole point of the split: it records lineage, it does not
    # choose a directory. --model used to do both.
    archive = init_archive_dir(tmp_path)

    pull(archive, monkeypatch, fake_hub_factory, "--base-model", ASSERTED_BASE)

    assert (model_dir(archive) / "gguf" / "tiny-chat-Q4_K_M.gguf").is_file()
    assert not (archive / "models" / "meta-models").exists()


@pytest.mark.parametrize(
    "claim", ["noslash", "acme/", "/repo", "acme/a/b", "../../etc/passwd", "acme/bad repo"]
)
def test_a_base_model_that_is_not_a_repo_id_is_refused(
    tmp_path, monkeypatch, fake_hub_factory, claim
):
    # The value is rendered into MODEL-RECORD.md and, from pass 5, read
    # back as a lineage pointer — it cannot be free text.
    archive = init_archive_dir(tmp_path)

    result = pull(archive, monkeypatch, fake_hub_factory, "--base-model", claim)

    assert result.exit_code == 2, click.unstyle(result.output)
    assert not model_dir(archive).exists()


def test_an_asserted_base_survives_a_repull_that_declares_a_card_base(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Re-pulling must not silently downgrade a curator's judgment to
    # whatever the card happens to say today.
    archive = init_archive_dir(tmp_path)
    pull(archive, monkeypatch, fake_hub_factory, "--base-model", ASSERTED_BASE)

    pull(archive, monkeypatch, fake_hub_factory)

    record = load_record(model_dir(archive))
    assert (record.base_model, record.base_model_source) == (ASSERTED_BASE, "asserted")
