"""Spec 0026 pass 5: ``pull --progress`` through the CLI.

Criteria 1, 3 and 4 end to end: with the flag, a pull logs progress
lines on ``llm_preserver.pull_progress`` beside ``downloading n of m``
and one summary line before ``pulled ... into ...``; without it, output
is unchanged, and the differential test pins that the two differ only
by those lines. No summary for a pull that moved nothing or stopped.

Harness: ``progress_cli_shapes`` (driven ticker, fixed clock, a hub
double that ticks mid-transfer); archives live in ``tmp_path``.
"""

import json

import click
from progress_cli_shapes import (
    PREFIX,
    Q4_BYTES,
    Q4_NAME,
    Q4_TICK_LINE,
    README_NAME,
    SUMMARY_LINE,
    TYPED,
    HookedHub,
    install_driven_ticker,
    install_fixed_clock,
    install_hub,
    lines_of,
    model_dir,
    new_archive,
    progress_lines,
    pull,
    runner,
    tick_after,
    ticker_threads,
)

import llm_preserver.cli.pull_exec.flow as flow
from llm_preserver.cli import app
from llm_preserver.hub import PullHubError


def ticking_hub(monkeypatch, **kwargs):
    """A hub whose Q4 download ticks the ticker once, at TICK_AT."""
    stops = install_driven_ticker(monkeypatch)
    clock = install_fixed_clock(monkeypatch)
    client = HookedHub(on_downloaded=tick_after(Q4_NAME, stops, clock), **kwargs)
    install_hub(monkeypatch, client)
    return client, stops


# --- the flag exists -------------------------------------------------------


def test_pull_help_lists_the_progress_flag():
    result = runner.invoke(app, ["pull", "--help"])

    assert result.exit_code == 0
    assert "--progress" in click.unstyle(result.output)


# --- with the flag: lines, summary, and the meter reaching the hub ---------


def test_a_tick_mid_transfer_prints_the_spec_shaped_progress_line(tmp_path, monkeypatch):
    ticking_hub(monkeypatch)

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    lines = lines_of(result)
    assert lines.count(Q4_TICK_LINE) == 1
    # Beside the existing per-file line, after the file it describes started.
    start = lines.index(f"INFO llm_preserver.pull_transfer: downloading 1 of 2: {Q4_NAME}")
    assert start < lines.index(Q4_TICK_LINE)


def test_a_completed_pull_logs_one_summary_line_before_the_pulled_line(tmp_path, monkeypatch):
    ticking_hub(monkeypatch)

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    lines = lines_of(result)
    assert lines.count(SUMMARY_LINE) == 1
    pulled = [i for i, line in enumerate(lines) if line.startswith("pulled ")]
    assert len(pulled) == 1
    assert lines.index(SUMMARY_LINE) < pulled[0]
    assert lines[-1].startswith("pulled ")  # the summary does not displace the final line


def test_the_flag_hands_a_meter_to_every_download(tmp_path, monkeypatch):
    client, _ = ticking_hub(monkeypatch)

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert [c["filename"] for c in client.download_kwargs] == [Q4_NAME, README_NAME]
    assert all(c.get("progress") is not None for c in client.download_kwargs)


def test_the_ticker_waits_on_the_thirty_second_interval(tmp_path, monkeypatch):
    _, stops = ticking_hub(monkeypatch)

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert len(stops) == 1
    assert stops[0].timeouts and set(stops[0].timeouts) == {30}


def test_no_ticker_thread_survives_a_successful_pull(tmp_path, monkeypatch):
    client, _ = ticking_hub(monkeypatch)

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert client.tickers_seen == [1, 1]  # positive control: it ran during transfer
    assert ticker_threads() == []


def test_the_ticker_is_not_running_while_a_confirmation_is_asked(tmp_path, monkeypatch):
    # "the first line prints one interval after the transfer starts,
    # never during prompts" (plan checkpoint): the thread is armed at
    # the transfer-start seam, after every confirmation.
    ticking_hub(monkeypatch)
    alive_at_prompt: list[int] = []
    real_confirm = flow.confirm_or_stop

    def observing_confirm(prompt, assume_yes):
        alive_at_prompt.append(len(ticker_threads()))
        return real_confirm(prompt, assume_yes)

    monkeypatch.setattr(flow, "confirm_or_stop", observing_confirm)

    result = pull(new_archive(tmp_path), "--include", "*Q4_K_M*", "--progress", stdin="y\n")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert alive_at_prompt == [0]  # the size confirmation was asked, with no ticker


# --- without the flag: nothing new (criterion 4) ----------------------------


def test_without_the_flag_no_meter_reaches_download_and_no_progress_text_prints(
    tmp_path, monkeypatch
):
    client, stops = ticking_hub(monkeypatch)

    result = pull(new_archive(tmp_path), *TYPED)

    assert result.exit_code == 0, click.unstyle(result.output)
    assert all("progress" not in call for call in client.download_kwargs)
    out = click.unstyle(result.output)
    assert "llm_preserver.pull_progress" not in out
    assert "progress:" not in out
    assert stops == []  # no ticker was even built


def test_with_and_without_the_flag_differ_only_by_progress_lines(tmp_path, monkeypatch):
    ticking_hub(monkeypatch)
    plain, metered = new_archive(tmp_path / "plain"), new_archive(tmp_path / "metered")

    without = pull(plain, *TYPED)
    with_flag = pull(metered, *TYPED, "--progress")

    assert without.exit_code == with_flag.exit_code == 0

    def normalized(result, archive):
        text = click.unstyle(result.output)
        text = text.replace(str(archive.resolve()), "<ARCHIVE>").replace(str(archive), "<ARCHIVE>")
        return text.splitlines()

    plain_lines = normalized(without, plain)
    metered_lines = normalized(with_flag, metered)
    added = progress_lines(metered_lines)
    assert added == [Q4_TICK_LINE, SUMMARY_LINE]  # positive control: lines were added
    assert [line for line in metered_lines if not line.startswith(PREFIX)] == plain_lines


# --- no summary when nothing transferred or the pull stopped ---------------


def test_a_no_op_repull_prints_no_progress_or_summary(tmp_path, monkeypatch):
    ticking_hub(monkeypatch)
    archive = new_archive(tmp_path)
    assert pull(archive, *TYPED).exit_code == 0

    result = pull(archive, *TYPED, "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert "nothing new to pull" in click.unstyle(result.output)
    assert progress_lines(lines_of(result)) == []


def test_a_metadata_only_repull_prints_no_progress_or_summary(tmp_path, monkeypatch):
    ticking_hub(monkeypatch)
    archive = new_archive(tmp_path)
    assert pull(archive, *TYPED).exit_code == 0

    result = pull(archive, *TYPED, "--role", "embedding", "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert "recorded the metadata" in click.unstyle(result.output)
    assert progress_lines(lines_of(result)) == []


def test_an_adopt_only_pull_prints_no_progress_or_summary(tmp_path, monkeypatch):
    client, _ = ticking_hub(monkeypatch, files=[(Q4_NAME, Q4_BYTES, True)])
    archive = new_archive(tmp_path)
    on_disk = model_dir(archive) / "gguf" / Q4_NAME
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(Q4_BYTES)

    result = pull(archive, *TYPED, "--progress")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert client.download_calls == []
    assert json.loads((model_dir(archive) / "model-record.json").read_text())  # adopted
    assert progress_lines(lines_of(result)) == []


def test_a_declined_size_confirmation_prints_no_progress_or_summary(tmp_path, monkeypatch):
    client, _ = ticking_hub(monkeypatch)

    result = pull(new_archive(tmp_path), "--include", "*Q4_K_M*", "--progress", stdin="n\n")

    assert result.exit_code == 0, click.unstyle(result.output)
    assert "nothing pulled:" in click.unstyle(result.output)
    assert client.download_calls == []
    assert progress_lines(lines_of(result)) == []


def test_a_failed_pull_prints_no_summary_and_leaves_no_ticker(tmp_path, monkeypatch):
    client, _ = ticking_hub(
        monkeypatch, download_error=PullHubError("hub said no"), fail_after_downloads=1
    )

    result = pull(new_archive(tmp_path), *TYPED, "--progress")

    assert result.exit_code == 4, click.unstyle(result.output)
    lines = lines_of(result)
    assert lines.count(Q4_TICK_LINE) == 1  # positive control: the ticker ran
    assert [line for line in progress_lines(lines) if "transferred " in line] == []
    assert client.tickers_seen == [1, 1]
    assert ticker_threads() == []
