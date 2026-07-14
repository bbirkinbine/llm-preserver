"""Resume-command hint — spec 0007.

When a pull's shape was assembled interactively (the discover handoff
or pull's interactive file listing), the CLI prints one copy-paste-ready
direct ``pull`` command after the confirmations succeed and before the
first byte transfers, and prints it again as the final line when the
transfer is interrupted with Ctrl-C (exit 130). Because pull is
idempotent over already-archived files (the skip matrix in
``pull_plan.plan_downloads``), that printed command doubles as the
resume command.

CliRunner + faked hub seam; no network; archives are tmp dirs; output
unstyled before substring asserts (rich ANSI in CI).
"""

import contextlib
import shlex

import click
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.hub import PullHubError
from llm_preserver.hub_discovery import ModelSummary

runner = CliRunner()

ENV_VAR = "LLM_PRESERVER_ARCHIVE"
QUANT_REPO = "bartowski/tiny-chat-GGUF"  # the conftest default repo
BASE_MODEL = "acme/tiny-chat"  # its declared base_model; the confirmed grouping default

# The hint's lead-in (spec 0007: exact wording is implementer-defined,
# pinned here so tests and implementation agree on one greppable line).
LEAD_IN = "to continue this pull later"


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def unstyled_output(result) -> str:
    return click.unstyle(combined_output(result))


def hint_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if LEAD_IN in line]


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def type_lines(*lines) -> str:
    """One stdin answer per prompt, newline-terminated."""
    return "".join(f"{line}\n" for line in lines)


def summary(repo_id, **overrides):
    """Build a ModelSummary with all-None facts unless overridden."""
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def quant_client(fake_hub_factory, **overrides):
    """One search hit (the conftest quant repo) declaring BASE_MODEL.

    Tree picks: 0 = pull this repo (the stable key). Pull's grouping
    default proposes BASE_MODEL (GGUF conversion).
    """
    defaults = dict(
        search_results=[summary(QUANT_REPO, downloads=41, base_model=BASE_MODEL)],
        summaries={BASE_MODEL: summary(BASE_MODEL, downloads=999)},
    )
    defaults.update(overrides)
    return fake_hub_factory(**defaults)


def invoke_discover(archive, *args, stdin=None):
    return runner.invoke(app, ["discover", "tiny", str(archive), *args], input=stdin)


def invoke_pull(archive, *args, stdin=None, env=None):
    return runner.invoke(app, ["pull", QUANT_REPO, str(archive), *args], input=stdin, env=env)


# Discover stdin scripts: search pick 1 -> tree pick 0 (pull this repo)
# -> archive mode (1 = pick files, 2 = whole repo) -> [patterns] ->
# grouping confirm -> [every-weight confirm when the patterns cover
# all weights] -> size confirm. --plan never confirms.
DISCOVER_WHOLE_REPO_STDIN = type_lines("1", "0", "2", "y", "y")
DISCOVER_PICK_FILES_STDIN = type_lines("1", "0", "1", "*Q4_K_M*,*Q8_0*", "y", "y", "y")
DISCOVER_WHOLE_REPO_PLAN_STDIN = type_lines("1", "0", "2")

# Direct pull, interactive file listing: patterns -> grouping -> size.
PULL_INTERACTIVE_STDIN = type_lines("*Q4_K_M*", "y", "y")


# --- discover handoff -------------------------------------------------


def test_discover_whole_repo_handoff_prints_one_full_resume_command(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The spec's headline line: one greppable hint carrying the exact
    # direct-pull shape — repo id, absolute archive path, mode flag,
    # and the human-confirmed grouping as --model.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, quant_client(fake_hub_factory))

    result = invoke_discover(archive, stdin=DISCOVER_WHOLE_REPO_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    hint = hints[0]
    assert "llm-preserver pull" in hint
    assert QUANT_REPO in hint
    assert str(archive.resolve()) in hint
    assert "--whole-repo" in hint
    assert f"--model {BASE_MODEL}" in hint


def test_hint_is_already_printed_when_the_first_transfer_fails(
    tmp_path, monkeypatch, fake_hub_factory
):
    # "Before the first byte transfers": even when download #1 dies
    # hub-side (exit 4), the hint is in the scrollback.
    archive = init_archive_dir(tmp_path)
    client = quant_client(
        fake_hub_factory,
        download_error=PullHubError("hub fell over"),
        fail_after_downloads=0,
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=DISCOVER_WHOLE_REPO_STDIN)

    assert result.exit_code == 4
    assert hint_lines(unstyled_output(result))


def test_discover_pick_files_hint_carries_each_pattern_as_quoted_include(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Pick-files mode: every entered pattern rides as a repeated
    # --include flag, shlex-quoted so paste-and-run cannot glob.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, quant_client(fake_hub_factory))

    result = invoke_discover(archive, stdin=DISCOVER_PICK_FILES_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    hint = hints[0]
    assert f"--include {shlex.quote('*Q4_K_M*')}" in hint
    assert f"--include {shlex.quote('*Q8_0*')}" in hint
    assert "--whole-repo" not in hint


def test_discover_plan_hint_omits_model_and_plan_flags(tmp_path, monkeypatch, fake_hub_factory):
    # Plan mode records confirmations instead of asking, so no grouping
    # was human-confirmed: the hint must not bake in --model (0006
    # adjudication), and the follow-up the user wants is the real pull,
    # so no --plan either.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, quant_client(fake_hub_factory))

    result = invoke_discover(archive, "--plan", stdin=DISCOVER_WHOLE_REPO_PLAN_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    hint = hints[0]
    assert "--whole-repo" in hint
    assert QUANT_REPO in hint
    assert str(archive.resolve()) in hint
    assert "--model" not in hint
    assert "--plan" not in hint


# --- pull's interactive file listing ----------------------------------


def test_pull_interactive_listing_prints_hint_with_include_and_model(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, stdin=PULL_INTERACTIVE_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    hint = hints[0]
    assert "llm-preserver pull" in hint
    assert QUANT_REPO in hint
    assert str(archive.resolve()) in hint
    assert f"--include {shlex.quote('*Q4_K_M*')}" in hint
    assert f"--model {BASE_MODEL}" in hint


def test_pull_plan_with_interactive_listing_hint_omits_model_and_plan(
    tmp_path, monkeypatch, fake_hub_factory
):
    # A plan whose shape was navigated interactively has the same
    # reconstruction problem; same omissions as the discover plan hint.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--plan", stdin=type_lines("*Q4_K_M*"))

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    hint = hints[0]
    assert f"--include {shlex.quote('*Q4_K_M*')}" in hint
    assert "--model" not in hint
    assert "--plan" not in hint


# --- the archive path is explicit and absolute ------------------------


def test_hint_resolves_a_relative_positional_path_to_absolute(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The hint must work from any working directory: a relative
    # positional archive path prints resolved.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["pull", QUANT_REPO, "archive"], input=PULL_INTERACTIVE_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    assert str(archive.resolve()) in hints[0]


def test_hint_prints_absolute_path_when_archive_comes_from_env_var(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The hint must work in a shell without LLM_PRESERVER_ARCHIVE set:
    # an env-var-only invocation still prints the path, absolute.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["pull", QUANT_REPO],
        input=PULL_INTERACTIVE_STDIN,
        env={ENV_VAR: "archive"},
    )

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    assert str(archive.resolve()) in hints[0]


# --- no hint when the user typed the shape themselves ------------------


def test_no_hint_when_user_passed_whole_repo_themselves(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--whole-repo", "--model", BASE_MODEL, "--yes")

    assert result.exit_code == 0
    assert LEAD_IN not in unstyled_output(result)


def test_no_hint_when_user_passed_include_themselves(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--include", "*Q4_K_M*", "--model", BASE_MODEL, "--yes")

    assert result.exit_code == 0
    assert LEAD_IN not in unstyled_output(result)


# --- Ctrl-C during the transfer ----------------------------------------


def test_ctrl_c_during_transfer_exits_130_with_hint_as_final_line(
    tmp_path, monkeypatch, fake_hub_factory
):
    # KeyboardInterrupt after file 1 of 2 has transferred: exit
    # 128 + SIGINT, the hint sits directly above the next shell prompt.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        download_error=KeyboardInterrupt(),
        fail_after_downloads=1,
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, stdin=PULL_INTERACTIVE_STDIN)

    assert result.exit_code == 130
    # Final line of stdout (stderr carries the transfer logging, whose
    # placement in combined output is click-version dependent).
    final_line = click.unstyle(result.output).rstrip().splitlines()[-1]
    assert LEAD_IN in final_line
    assert QUANT_REPO in final_line
    assert str(archive.resolve()) in final_line


def test_ctrl_c_on_a_user_typed_pull_still_prints_the_hint(tmp_path, monkeypatch, fake_hub_factory):
    # A user-typed shape gets no transfer-start print (shell history
    # has it) — but Ctrl-C always ends with the continue line, carrying
    # the resolved --model that history may lack (live-use
    # adjudication 2026-07-13: a resumed pull's second interrupt
    # printed nothing, and silence read as a miss).
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        download_error=KeyboardInterrupt(),
        fail_after_downloads=1,
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--whole-repo", "--model", BASE_MODEL, "--yes")

    assert result.exit_code == 130
    hints = hint_lines(click.unstyle(result.output))
    assert len(hints) == 1  # the Ctrl-C print only — nothing at transfer start
    final_line = click.unstyle(result.output).rstrip().splitlines()[-1]
    assert LEAD_IN in final_line
    assert "--whole-repo" in final_line
    assert f"--model {BASE_MODEL}" in final_line


def test_ctrl_c_is_never_swallowed_into_a_retry(tmp_path, monkeypatch, fake_hub_factory):
    # No retry, no traceback: the interrupted file was attempted exactly
    # once and the run ended there.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        download_error=KeyboardInterrupt(),
        fail_after_downloads=1,
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, stdin=PULL_INTERACTIVE_STDIN)

    assert result.exit_code == 130
    assert len(client.download_calls) == 2  # file 1 ok, file 2 interrupted once
    assert "Traceback" not in combined_output(result)


# --- flags that shape the pull ride along; --yes never does ------------


def test_yes_never_appears_in_the_hint(tmp_path, monkeypatch, fake_hub_factory):
    # The re-run asks its own size confirmation; --yes never rides.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    # --yes auto-accepts the size confirm; grouping still takes a y.
    result = invoke_pull(archive, "--yes", stdin=type_lines("*Q4_K_M*", "y"))

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    assert "--yes" not in hints[0]


def test_role_and_refresh_docs_ride_along_in_the_hint(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--role", "chat", "--refresh-docs", stdin=PULL_INTERACTIVE_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    assert "--role chat" in hints[0]
    assert "--refresh-docs" in hints[0]


def test_hf_logging_rides_along_in_the_hint(tmp_path, monkeypatch, fake_hub_factory):
    # Spec 0008: the flag exists for the stalled-transfer scenario the
    # hint serves, so the continue command must not silently drop it.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--hf-logging", stdin=PULL_INTERACTIVE_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    assert "--hf-logging" in hints[0]


def test_discover_hf_logging_rides_along_in_the_hint(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, quant_client(fake_hub_factory))

    result = invoke_discover(archive, "--hf-logging", stdin=DISCOVER_PICK_FILES_STDIN)

    assert result.exit_code == 0
    hints = hint_lines(unstyled_output(result))
    assert len(hints) == 1
    assert "--hf-logging" in hints[0]


# --- the hint round-trips: paste it and the pull completes --------------


def test_pasting_the_hint_after_ctrl_c_completes_the_pull(tmp_path, monkeypatch, fake_hub_factory):
    # The headline spec criterion, pinned end to end: interrupt a pull
    # mid-transfer, parse the printed hint back into argv, re-invoke —
    # the resumed run finishes with the same selection in the same
    # model directory, asking only its own size confirmation (--model
    # rides, so no grouping prompt; --yes never rides, so one y).
    archive = init_archive_dir(tmp_path)
    install_fake_hub(
        monkeypatch,
        fake_hub_factory(download_error=KeyboardInterrupt(), fail_after_downloads=1),
    )
    interrupted = invoke_pull(archive, stdin=PULL_INTERACTIVE_STDIN)
    assert interrupted.exit_code == 130
    hint = hint_lines(click.unstyle(interrupted.output))[0]

    # Everything after the lead-in is the command; drop the program
    # name and subcommand, keep its argv.
    command = hint.split(f"{LEAD_IN}: ", 1)[1]
    argv = shlex.split(command)
    assert argv[:2] == ["llm-preserver", "pull"]

    install_fake_hub(monkeypatch, fake_hub_factory())
    resumed = runner.invoke(app, ["pull", *argv[2:]], input=type_lines("y"))

    assert resumed.exit_code == 0
    output = click.unstyle(resumed.output)
    assert f"pulled {QUANT_REPO}" in output
    assert BASE_MODEL in output  # landed in the model dir the hint carried
    # The resumed shape is fully user-typed (from the paste): no hint.
    assert LEAD_IN not in output
    # The selected weight is on disk in the archive.
    assert list(archive.rglob("tiny-chat-Q4_K_M.gguf"))


# --- hub-supplied repo ids are validated before entering the hint ------


def test_no_hint_for_a_repo_id_not_shaped_like_one(tmp_path):
    # shlex quoting cannot defuse an argv token: a hub-supplied id like
    # --yes would parse as the flag on paste. Malformed ids compose no
    # hint at all.
    from llm_preserver.cli.resume_hint import compose_resume_hint

    assert compose_resume_hint("--yes", tmp_path, select_all=True) is None
    assert compose_resume_hint("-rf", tmp_path, select_all=True) is None
    assert compose_resume_hint("no-namespace", tmp_path, select_all=True) is None
    valid = compose_resume_hint(QUANT_REPO, tmp_path, select_all=True)
    assert valid is not None
    assert valid.startswith(f"{LEAD_IN}: llm-preserver pull {QUANT_REPO} ")
