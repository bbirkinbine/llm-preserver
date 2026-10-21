"""``pull --revision`` in the plan report and every composed command (spec 0024).

Criteria 13 and 16: the plan names the requested ref beside the commit
it resolved to, and every command the tool hands back — the plan
follow-up, the transfer-start resume hint, the Ctrl-C line, and the
spec 0020 ``--refresh-docs`` recovery — pins ``--revision <commit>``,
never the branch. A moving branch must not switch a resumed pull onto
another tree. Absence checks are differential: the same run without the
flag is the control.

CliRunner + a faked hub seam; no network; tmp archives only.

Expected red (test-first): ``pull`` has no ``--revision`` option yet.
"""

import hashlib
import json

from revision_shapes import (
    BRANCH,
    BRANCH_SHA,
    full_output,
    init_archive_dir,
    install_fake_hub,
    invoke_pull,
    revision_client,
    revision_lines,
    stdout_text,
)

PLAN_FOLLOW_UP = "to continue this pull later"
DOC_LEAD_IN = "to replace every changed documentation file and finish this pull"


def tree_snapshot(root) -> dict[str, str]:
    """Relative path -> sha256 for every file under root (write detector)."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def lines_with(text: str, needle: str) -> list[str]:
    return [line for line in text.splitlines() if needle in line]


def assert_pins_commit(line: str) -> None:
    assert f"--revision {BRANCH_SHA}" in line, line
    assert BRANCH not in line, line


# --- the plan report ---------------------------------------------------


def test_plan_names_the_branch_and_the_commit_it_resolved_to(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--include", "*model*", "--plan", "--revision", BRANCH)

    assert result.exit_code == 0, full_output(result)
    found = revision_lines(stdout_text(result))
    assert len(found) == 1
    assert BRANCH in found[0] and BRANCH_SHA in found[0]


def test_plan_for_a_full_sha_names_the_commit_once(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--include", "*model*", "--plan", "--revision", BRANCH_SHA)

    assert result.exit_code == 0, full_output(result)
    found = revision_lines(stdout_text(result))
    assert len(found) == 1
    assert found[0].count(BRANCH_SHA) == 1


def test_plan_with_a_revision_downloads_and_writes_nothing(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)
    before = tree_snapshot(archive)

    result = invoke_pull(archive, "--whole-repo", "--plan", "--revision", BRANCH)

    assert result.exit_code == 0, full_output(result)
    assert "model.safetensors" in stdout_text(result)  # the branch's tree was planned
    assert client.repo_info_revisions == [BRANCH]
    assert client.download_calls == []
    assert tree_snapshot(archive) == before


def test_plan_follow_up_pins_the_commit(tmp_path, monkeypatch, fake_hub_factory):
    # Interactive listing under --plan earns the follow-up command.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--plan", "--revision", BRANCH, stdin="*model*\n")

    assert result.exit_code == 0, full_output(result)
    hints = lines_with(stdout_text(result), PLAN_FOLLOW_UP)
    assert len(hints) == 1
    assert_pins_commit(hints[0])


# --- the resume hint ---------------------------------------------------


def test_transfer_start_hint_pins_the_commit(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--revision", BRANCH, stdin="*model*\ny\n")

    assert result.exit_code == 0, full_output(result)
    hints = lines_with(stdout_text(result), PLAN_FOLLOW_UP)
    assert len(hints) == 1
    assert_pins_commit(hints[0])


def test_ctrl_c_line_pins_the_commit(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = revision_client(
        fake_hub_factory, download_error=KeyboardInterrupt(), fail_after_downloads=1
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--whole-repo", "--yes", "--revision", BRANCH)

    assert result.exit_code == 130, full_output(result)
    final_line = stdout_text(result).rstrip().splitlines()[-1]
    assert PLAN_FOLLOW_UP in final_line
    assert_pins_commit(final_line)


# --- the spec 0020 doc-refresh recovery line ---------------------------


def test_doc_refresh_recovery_pins_the_commit(tmp_path, monkeypatch, fake_hub_factory):
    # Archive the default branch first (its README rides along), then
    # pull the branch, whose README differs: the spec 0020 doc stop.
    # The seeded artifact's commit is cleared, as for a legacy or
    # imported artifact: with a recorded commit the mixed-tree refusal
    # (test_cli_pull_revision_mixing) speaks first, and the doc stop is
    # reachable under --revision only for an artifact pinned to nothing.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))
    seeded = invoke_pull(archive, "--include", "*cal*", "--yes")
    assert seeded.exit_code == 0, full_output(seeded)
    record_path = archive / "models" / "turboderp" / "tiny-exl3" / "model-record.json"
    record = json.loads(record_path.read_text())
    for artifact in record["artifacts"]:
        artifact["revision"] = None
    record_path.chmod(0o644)
    record_path.write_text(json.dumps(record))

    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))
    result = invoke_pull(archive, "--include", "*model*", "--yes", "--revision", BRANCH)

    text = full_output(result)
    assert result.exit_code == 5, text
    recovery = lines_with(text, DOC_LEAD_IN)
    assert recovery
    assert_pins_commit(recovery[0])


# --- without the flag, nothing changes ---------------------------------


def test_no_hint_names_a_revision_when_none_was_requested(tmp_path, monkeypatch, fake_hub_factory):
    # Differential: the same three hint-bearing runs (plan follow-up,
    # transfer start, Ctrl-C), with and without --revision.
    def runs(root, *flag):
        archive = init_archive_dir(root)
        install_fake_hub(monkeypatch, revision_client(fake_hub_factory))
        plan = invoke_pull(archive, "--plan", *flag, stdin="*a*\n")
        real = invoke_pull(archive, *flag, stdin="*a*\ny\n")
        install_fake_hub(
            monkeypatch,
            revision_client(
                fake_hub_factory, download_error=KeyboardInterrupt(), fail_after_downloads=0
            ),
        )
        interrupted = invoke_pull(archive, "--whole-repo", "--yes", *flag)
        return [plan, real, interrupted]

    plain = runs(tmp_path / "plain")
    pinned = runs(tmp_path / "pinned", "--revision", BRANCH)

    for result in pinned:
        hints = lines_with(stdout_text(result), PLAN_FOLLOW_UP)
        assert hints, full_output(result)
        assert all(f"--revision {BRANCH_SHA}" in hint for hint in hints)
    for result in plain:
        hints = lines_with(stdout_text(result), PLAN_FOLLOW_UP)
        assert hints, full_output(result)
        assert not any("--revision" in hint for hint in hints)
