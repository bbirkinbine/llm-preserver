"""A declined confirmation is exit 0 too — spec 0021 (the two confirms).

Continues ``test_cli_pull_decline.py``, whose harness these tests
import. The size confirmation and the every-weight confirmation are the
other two human-answered declines in the pull flow; together with the
file-listing quit they are the complete set, and no other
``PullUserError`` site changes.

A scripted ``echo n | pull …`` moves from 2 to 0 with them. That is the
only non-interactive behavior change in the spec and it is deliberate:
such a run *answered* the question, so it is a decline, not an
unanswerable prompt. What stays exit 2 is the prompt nobody can answer —
and the two are separated by the interactive verdict, never by how the
answer arrived.
"""

from test_cli_pull_decline import (
    DECLINE_PREFIX,
    decline_line,
    init_archive_dir,
    install_fake_hub,
    invoke_pull,
    simulate_streams,
    stderr_of,
)

SIZE_DECLINE = "nothing pulled: size confirmation declined"

# One of two weights: the size confirmation is the only prompt.
ONE_WEIGHT = ("--include", "*Q4_K_M*")
# Both weights: the every-weight confirmation comes first.
EVERY_WEIGHT = ("--include", "*.gguf")


def test_declining_the_size_confirmation_exits_0_and_downloads_nothing(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, *ONE_WEIGHT, stdin="n\n")

    assert result.exit_code == 0
    assert decline_line(result) == SIZE_DECLINE
    assert "error [" not in stderr_of(result)
    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_eof_at_the_size_confirmation_declines_on_a_terminal_but_stays_a_fault_on_a_pipe(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Ctrl-D one prompt after the listing must not mean something else:
    # today a terminal is told "stdin is not interactive", which is
    # false on the surface the question was just printed to.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    scripted = invoke_pull(archive, *ONE_WEIGHT, stdin="")
    simulate_streams(monkeypatch)
    attended = invoke_pull(archive, *ONE_WEIGHT, stdin="")

    assert attended.exit_code == 0
    assert decline_line(attended) == SIZE_DECLINE
    assert scripted.exit_code == 2
    assert "--yes" in stderr_of(scripted)


def test_declining_the_every_weight_confirmation_exits_0_keeping_its_advice(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, *EVERY_WEIGHT, stdin="n\n")

    assert result.exit_code == 0
    line = decline_line(result)
    assert line.startswith(DECLINE_PREFIX)
    assert "narrow --include and re-run" in line
    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_eof_at_the_every_weight_confirmation_declines_on_a_terminal_but_stays_a_fault_on_a_pipe(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    scripted = invoke_pull(archive, *EVERY_WEIGHT, stdin="")
    simulate_streams(monkeypatch)
    attended = invoke_pull(archive, *EVERY_WEIGHT, stdin="")

    assert attended.exit_code == 0
    assert "narrow --include and re-run" in decline_line(attended)
    assert scripted.exit_code == 2
    assert "narrow --include, or run interactively" in stderr_of(scripted)


def test_yes_never_answers_the_every_weight_confirmation(tmp_path, monkeypatch, fake_hub_factory):
    # --yes auto-accepts the size confirmation only. If it reached this
    # prompt the pull would proceed and the weights would land.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, *EVERY_WEIGHT, "--yes", stdin="n\n")

    assert result.exit_code == 0
    assert "narrow --include and re-run" in decline_line(result)
    assert client.download_calls == []
