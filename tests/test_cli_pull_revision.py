"""``pull --revision`` end to end through the CLI (spec 0024).

The repo shape (``revision_shapes``) keeps calibration material on the
default branch and the model on ``4.05bpw``, so which files land in the
archive says which tree the pull read. CliRunner + a faked hub seam; no
network; archives are tmp dirs; output unstyled before any assert, and
lines counted on ``result.output`` alone.

Expected red (test-first): ``pull`` has no ``--revision`` option, so
every run passing it is a click usage error (exit 2, no pull).
"""

import click
import pytest
from revision_shapes import (
    BRANCH,
    BRANCH_SHA,
    MAIN_SHA,
    REPO_ID,
    full_output,
    init_archive_dir,
    install_fake_hub,
    invoke_pull,
    model_dir,
    read_record,
    recorded_names,
    revision_client,
    revision_lines,
    runner,
    stdout_text,
)

from llm_preserver.cli import app


def test_help_names_branch_tag_and_commit_and_the_immutable_pin():
    result = runner.invoke(app, ["pull", "--help"], terminal_width=200)

    text = " ".join(click.unstyle(result.output).split()).lower()
    assert result.exit_code == 0
    assert "--revision" in text
    for word in ("branch", "tag", "commit sha", "immutable commit"):
        assert word in text, word


@pytest.mark.parametrize("value", ["", "   ", "\t", ".", ".."])
def test_a_blank_revision_exits_2_before_any_client_is_built(tmp_path, monkeypatch, value):
    import llm_preserver.cli as cli_module

    def refuse_to_build():
        raise AssertionError("a blank --revision must be refused before the client is built")

    archive = init_archive_dir(tmp_path)
    monkeypatch.setattr(cli_module, "HubClient", refuse_to_build)

    result = invoke_pull(archive, "--whole-repo", "--yes", "--revision", value)

    text = full_output(result)
    assert result.exit_code == 2, text
    # The tool's own refusal, not click's "No such option" usage error.
    assert "error [user input]" in text
    assert "--revision" in text
    assert not isinstance(result.exception, AssertionError)


def test_owner_repo_at_ref_is_still_an_invalid_repo_id(tmp_path, monkeypatch, fake_hub_factory):
    # Criterion 3, a guard against shorthand creeping in. The real
    # validator must see the "@", so the fake defers to it.
    from huggingface_hub.utils import validate_repo_id

    from llm_preserver.hub import PullInvalidIdError

    client = revision_client(fake_hub_factory)
    original = client.repo_info

    def validating_repo_info(repo_id, revision=None):
        try:
            validate_repo_id(repo_id)
        except ValueError as exc:
            raise PullInvalidIdError(str(exc)) from exc
        return original(repo_id, revision)

    client.repo_info = validating_repo_info
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, client)

    shorthand = invoke_pull(archive, "--whole-repo", "--yes", repo=f"{REPO_ID}@{BRANCH}")
    flagged = invoke_pull(archive, "--whole-repo", "--yes", "--revision", BRANCH)

    assert shorthand.exit_code == 2
    assert flagged.exit_code == 0, full_output(flagged)  # the supported spelling works


def test_the_inventory_comes_from_the_requested_revision(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--whole-repo", "--yes", "--revision", BRANCH)

    assert result.exit_code == 0, full_output(result)
    assert client.repo_info_revisions == [BRANCH]
    assert set(client.download_calls) == {
        "model.safetensors",
        "config.json",
        "chat_template.jinja",
        "README.md",
    }
    assert "cal_trace.safetensors" not in recorded_names(read_record(archive))


def test_every_download_receives_the_resolved_commit(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--whole-repo", "--yes", "--revision", BRANCH)

    assert result.exit_code == 0, full_output(result)
    assert client.download_revisions
    assert {revision for _name, revision in client.download_revisions} == {BRANCH_SHA}


def test_the_record_pins_the_resolved_commit_never_the_branch(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--whole-repo", "--yes", "--revision", BRANCH)

    assert result.exit_code == 0, full_output(result)
    record = read_record(archive)
    assert [artifact["revision"] for artifact in record["artifacts"]] == [BRANCH_SHA]
    pins = {entry["revision"] for artifact in record["artifacts"] for entry in artifact["files"]}
    assert pins == {BRANCH_SHA}
    assert BRANCH not in (model_dir(archive) / "model-record.json").read_text()


def test_the_resolution_line_prints_once_before_the_listing(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--revision", BRANCH, stdin="*model*\ny\n")

    assert result.exit_code == 0, full_output(result)
    lines = stdout_text(result).splitlines()
    found = revision_lines("\n".join(lines))
    assert len(found) == 1
    assert BRANCH in found[0] and BRANCH_SHA in found[0]
    listing = next(i for i, line in enumerate(lines) if f"files in {REPO_ID}" in line)
    assert lines.index(found[0]) < listing


def test_the_resolution_line_prints_before_the_size_prompt(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--include", "*model*", "--revision", BRANCH, stdin="y\n")

    assert result.exit_code == 0, full_output(result)
    text = stdout_text(result)
    found = revision_lines(text)
    assert len(found) == 1
    assert text.index(found[0]) < text.index(f"from {REPO_ID}?")


def test_without_the_flag_the_default_call_and_output_are_unchanged(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Differential: the same repo pulled twice, into separate archives,
    # differing only in --revision.
    plain_client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, plain_client)
    plain = invoke_pull(init_archive_dir(tmp_path / "a"), "--whole-repo", "--yes")

    pinned_client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, pinned_client)
    pinned = invoke_pull(
        init_archive_dir(tmp_path / "b"), "--whole-repo", "--yes", "--revision", BRANCH
    )

    assert plain.exit_code == 0 and pinned.exit_code == 0, full_output(pinned)
    assert plain_client.repo_info_revisions == [None]
    assert pinned_client.repo_info_revisions == [BRANCH]
    assert revision_lines(stdout_text(plain)) == []
    assert len(revision_lines(stdout_text(pinned))) == 1
    assert {rev for _name, rev in plain_client.download_revisions} == {MAIN_SHA}


@pytest.mark.parametrize("shape", [("--whole-repo",), ("--include", "*model*")])
def test_a_missing_revision_exits_2_and_touches_nothing(
    tmp_path, monkeypatch, fake_hub_factory, shape
):
    archive = init_archive_dir(tmp_path)
    client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, *shape, "--yes", "--revision", "no-such-branch")

    assert result.exit_code == 2, full_output(result)
    assert client.repo_info_revisions == ["no-such-branch"]  # it reached the hub
    assert "error [user input]" in full_output(result)
    assert "Traceback" not in full_output(result)
    assert client.download_calls == []
    assert not (archive / ".staging").exists()
    assert not model_dir(archive).exists()


@pytest.mark.parametrize(
    "extra",
    [
        ("--include", "*model*", "--yes"),
        ("--whole-repo", "--yes"),
        ("--include", "*model*", "--yes", "--role", "chat"),
        ("--include", "*model*", "--yes", "--base-model", "acme/glm"),
    ],
)
def test_revision_composes_with_the_other_pull_flags(
    tmp_path, monkeypatch, fake_hub_factory, extra
):
    archive = init_archive_dir(tmp_path)
    client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, *extra, "--revision", BRANCH)

    assert result.exit_code == 0, full_output(result)
    record = read_record(archive)
    assert "model.safetensors" in recorded_names(record)
    assert {rev for _name, rev in client.download_revisions} == {BRANCH_SHA}
    if "--role" in extra:
        assert record["roles"] == ["chat"]
    if "--base-model" in extra:
        assert "acme/glm" in (model_dir(archive) / "model-record.json").read_text()


def test_the_interactive_listing_without_a_revision_makes_the_positional_call(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Criterion 2 on the path production uses: a seam double predating
    # spec 0024 (no revision parameter at all) still drives the
    # interactive listing. Sending revision=None here would TypeError.
    template = fake_hub_factory()
    base_cls = type(template)

    class PositionalOnlyClient(base_cls):
        def repo_info(self, repo_id):
            return super().repo_info(repo_id)

    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, PositionalOnlyClient(files=template.files, base_model=None))

    result = invoke_pull(archive, stdin="*\ny\ny\n", repo="bartowski/tiny-chat-GGUF")

    assert result.exit_code == 0, full_output(result)
