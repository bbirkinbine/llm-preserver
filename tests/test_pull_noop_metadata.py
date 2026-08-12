"""Metadata flags on a nothing-to-do pull — live use, 2026-08-11.

Spec 0014 makes a fully-archived re-pull a no-op: no prompts, no
record write, exit 0. Correct for *downloads* — but ``--role`` and
``--base-model`` are not downloads. They are record edits the curator
asked for, and the no-op path returned before ``update_record`` ran, so
both were accepted at the command line and silently discarded.

Found by running the flag against the real archive: the assertion was
dropped, the record kept saying ``base_model_source: card``, and the
command exited 0 saying "nothing new to pull". Silently ignoring an
explicit instruction is the same class as spec 0014's own silent-exit-0
finding.

Already-archived is exactly when you most want to *correct* a lineage
claim, so the flags have to work there or they do not work at all.
"""

import pytest

from llm_preserver.pull import pull_model
from llm_preserver.records import load_record

REPO_ID = "bartowski/tiny-chat-GGUF"


def do_pull(archive, client, **kwargs):
    kwargs.setdefault("include", ["*Q4_K_M*"])
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull_model(archive, REPO_ID, client, **kwargs)


@pytest.fixture
def archived(tmp_path, fake_hub_factory):
    """An archive where the whole selection is already present."""
    from llm_preserver.archive import init_archive

    archive = tmp_path / "archive"
    init_archive(archive)
    do_pull(archive, fake_hub_factory())
    return archive


def test_a_no_op_pull_records_an_asserted_base_model(archived, fake_hub_factory):
    do_pull(archived, fake_hub_factory(), base_model="meta/other-base")

    record = load_record(archived / "models" / "bartowski" / "tiny-chat-GGUF")
    assert (record.base_model, record.base_model_source) == ("meta/other-base", "asserted")


def test_a_no_op_pull_records_new_roles(archived, fake_hub_factory):
    # Same hole, older: --role has been silently dropped on a no-op
    # since spec 0014 shipped.
    do_pull(archived, fake_hub_factory(), roles=["coding"])

    record = load_record(archived / "models" / "bartowski" / "tiny-chat-GGUF")
    assert "coding" in record.roles


def test_a_no_op_pull_with_no_metadata_flags_writes_nothing(archived, fake_hub_factory):
    # The 0014 contract stands where it should: a plain re-pull still
    # moves no bytes and rewrites no record.
    record_path = archived / "models" / "bartowski" / "tiny-chat-GGUF" / "model-record.json"
    before = record_path.read_bytes()

    do_pull(archived, fake_hub_factory())

    assert record_path.read_bytes() == before


def test_asserting_the_value_the_card_gave_still_upgrades_the_attribution(
    archived, fake_hub_factory
):
    """The claim is unchanged; the authority behind it is not. Recording
    ``card`` when a human just vouched for it loses the distinction
    ``base_model_source`` exists to keep."""
    do_pull(archived, fake_hub_factory(), base_model="acme/tiny-chat")  # what the card declares

    record = load_record(archived / "models" / "bartowski" / "tiny-chat-GGUF")
    assert (record.base_model, record.base_model_source) == ("acme/tiny-chat", "asserted")


def test_the_final_line_does_not_claim_a_pull_that_did_not_happen(
    tmp_path, fake_hub_factory, monkeypatch
):
    """0014's principle: a run that moved no bytes must not read as one
    that did. A metadata-only write is a third outcome — not a pull,
    not a no-op — and needs its own line."""
    import click
    from typer.testing import CliRunner

    import llm_preserver.cli as cli_module
    from llm_preserver.cli import app

    runner = CliRunner()
    archive = tmp_path / "archive"
    archive.mkdir()
    runner.invoke(app, ["init", str(archive)])
    monkeypatch.setattr(cli_module, "HubClient", lambda: fake_hub_factory())
    args = ["pull", REPO_ID, str(archive), "--include", "*Q4_K_M*", "--yes"]
    runner.invoke(app, args)

    result = runner.invoke(app, [*args, "--base-model", "meta/other-base"])

    out = click.unstyle(result.output)
    assert "recorded the metadata" in out
    assert f"pulled {REPO_ID} into" not in out


def test_reasserting_an_identical_claim_writes_nothing(archived, fake_hub_factory):
    """Mutation survivor (review, 2026-08-11): forcing the change check
    to True left the suite green. The spec adjudicates that asserting an
    identical claim *with identical attribution* stays a true no-op —
    otherwise every scripted re-pull rewrites records for nothing."""
    import llm_preserver.pull as pull_module

    do_pull(archived, fake_hub_factory(), base_model="meta/other-base")
    writes: list[object] = []
    real_save = pull_module.save_record
    pull_module.save_record = lambda record, model_dir: (
        writes.append(model_dir),
        real_save(record, model_dir),
    )[1]
    try:
        do_pull(archived, fake_hub_factory(), base_model="meta/other-base")
    finally:
        pull_module.save_record = real_save

    # Counted, not compared: rewriting identical content produces
    # identical bytes, so a byte check cannot tell a no-op from a
    # pointless write (and did not, when this test was first written).
    assert writes == []
