"""Spec 0026 pass 5: composed commands carry ``--progress``; discover passes it on.

Criterion 7: the resume hint (spec 0007), the doc-refresh recovery
command (spec 0020) and the ``--plan`` follow-up replay ``--progress``
when the run used it, placed right after ``--hf-logging`` (the same
position rule spec 0008 set for that flag), and never otherwise.
``--plan --progress`` prints no progress lines. Criterion 8:
``discover`` accepts the flag and hands it to the pull flow; match mode
refuses it at exit 2 the way it refuses ``--plan``.

Harness: ``progress_cli_shapes``; archives live in ``tmp_path``.
"""

import os
import shlex

import click
import pytest
from progress_cli_shapes import (
    Q4_BYTES,
    Q4_NAME,
    README_NAME,
    REPO_ID,
    SUMMARY_LINE,
    HookedHub,
    install_driven_ticker,
    install_fixed_clock,
    install_hub,
    lines_of,
    new_archive,
    progress_lines,
    pull,
    reset_cli_logging,
    runner,
    tick_after,
)

from llm_preserver.cli import app
from llm_preserver.cli.resume_hint import compose_doc_refresh_hint, compose_resume_hint
from llm_preserver.hub_discovery import ModelSummary

RESUME = "to continue this pull later"
DOC_REFRESH = "to replace every changed documentation file and finish this pull"


def tail(line: str, n: int) -> list[str]:
    """The last ``n`` argv tokens of a composed command line."""
    return shlex.split(line.split(": ", 1)[1])[-n:]


def lines_with(result, lead_in: str) -> list[str]:
    return [line for line in lines_of(result) if lead_in in line]


@pytest.fixture
def restore_rust_log():
    """--hf-logging writes RUST_LOG; put it back (test_hf_logging.py's fixture)."""
    before = os.environ.get("RUST_LOG")
    yield
    if before is None:
        os.environ.pop("RUST_LOG", None)
    else:
        os.environ["RUST_LOG"] = before


# --- composition --------------------------------------------------------


def test_resume_hint_ends_with_progress_when_the_run_used_it(tmp_path):
    hint = compose_resume_hint(REPO_ID, tmp_path, include=["*Q4*"], progress=True)

    assert hint is not None
    assert tail(hint, 1) == ["--progress"]


def test_resume_hint_places_progress_right_after_hf_logging(tmp_path):
    hint = compose_resume_hint(
        REPO_ID, tmp_path, include=["*Q4*"], refresh_docs=True, hf_logging=True, progress=True
    )

    assert hint is not None
    assert tail(hint, 3) == ["--refresh-docs", "--hf-logging", "--progress"]


def test_resume_hint_omits_progress_by_default(tmp_path):
    hint = compose_resume_hint(REPO_ID, tmp_path, include=["*Q4*"], hf_logging=True)

    assert hint is not None
    assert "--progress" not in shlex.split(hint.split(": ", 1)[1])


def test_doc_refresh_command_carries_progress_after_hf_logging(tmp_path):
    only = compose_doc_refresh_hint(REPO_ID, tmp_path, include=["*Q4*"], progress=True)
    both = compose_doc_refresh_hint(
        REPO_ID, tmp_path, include=["*Q4*"], hf_logging=True, progress=True
    )

    assert only is not None and both is not None
    assert tail(only, 2) == ["--refresh-docs", "--progress"]
    assert tail(both, 3) == ["--refresh-docs", "--hf-logging", "--progress"]


# --- the CLI threads it through ------------------------------------------


def test_an_interactive_pull_prints_its_hint_with_progress(tmp_path, monkeypatch):
    stops = install_driven_ticker(monkeypatch)
    clock = install_fixed_clock(monkeypatch)
    install_hub(monkeypatch, HookedHub(on_downloaded=tick_after(Q4_NAME, stops, clock)))

    result = pull(new_archive(tmp_path), "--progress", stdin="*Q4_K_M*\ny\n")

    assert result.exit_code == 0, click.unstyle(result.output)
    hints = lines_with(result, RESUME)
    assert len(hints) == 1
    assert tail(hints[0], 1) == ["--progress"]


def test_plan_follow_up_carries_progress_and_no_progress_lines_print(
    tmp_path, monkeypatch, restore_rust_log
):
    stops = install_driven_ticker(monkeypatch)
    client = HookedHub()
    install_hub(monkeypatch, client)

    result = pull(new_archive(tmp_path), "--plan", "--hf-logging", "--progress", stdin="*Q4_K_M*\n")

    assert result.exit_code == 0, click.unstyle(result.output)
    hints = lines_with(result, RESUME)
    assert len(hints) == 1
    assert tail(hints[0], 2) == ["--hf-logging", "--progress"]
    assert "--plan" not in shlex.split(hints[0].split(": ", 1)[1])
    assert progress_lines(lines_of(result)) == []
    assert client.download_calls == []
    assert stops == []


def test_a_doc_refresh_stop_prints_a_recovery_command_with_progress(tmp_path, monkeypatch):
    archive = new_archive(tmp_path)
    v1 = [(Q4_NAME, Q4_BYTES, True), (README_NAME, b"# card\n", False)]
    install_hub(monkeypatch, HookedHub(files=v1))
    assert pull(archive, "--include", "*Q4_K_M*", "--yes").exit_code == 0
    v2 = [(Q4_NAME, Q4_BYTES, True), (README_NAME, b"# card, revised upstream\n", False)]
    install_hub(monkeypatch, HookedHub(files=v2))

    result = pull(archive, "--include", "*Q4_K_M*", "--yes", "--progress")

    assert result.exit_code == 5, click.unstyle(result.output)
    recovery = lines_with(result, DOC_REFRESH)
    assert len(recovery) == 1
    assert tail(recovery[0], 2) == ["--refresh-docs", "--progress"]


# --- discover ------------------------------------------------------------


def summary(repo_id: str) -> ModelSummary:
    return ModelSummary(
        repo_id=repo_id, downloads=41, last_modified=None, gated=None, base_model=None
    )


def discover(archive, *args, stdin):
    reset_cli_logging()
    return runner.invoke(app, ["discover", "tiny", str(archive), *args], input=stdin)


# search pick 1 -> tree 0 (pull this repo) -> 1 (pick files) -> patterns -> size y
DISCOVER_PICK_FILES = "1\n0\n1\n*Q4_K_M*\ny\ny\n"


def test_discover_help_lists_the_progress_flag():
    result = runner.invoke(app, ["discover", "--help"])

    assert result.exit_code == 0
    assert "--progress" in click.unstyle(result.output)


def test_discover_passes_progress_to_the_pull(tmp_path, monkeypatch):
    stops = install_driven_ticker(monkeypatch)
    clock = install_fixed_clock(monkeypatch)
    client = HookedHub(
        on_downloaded=tick_after(Q4_NAME, stops, clock), search_results=[summary(REPO_ID)]
    )
    install_hub(monkeypatch, client)

    result = discover(new_archive(tmp_path), "--progress", stdin=DISCOVER_PICK_FILES)

    assert result.exit_code == 0, click.unstyle(result.output)
    assert all(call.get("progress") is not None for call in client.download_kwargs)
    assert lines_of(result).count(SUMMARY_LINE) == 1
    hints = lines_with(result, RESUME)
    assert len(hints) == 1
    assert tail(hints[0], 1) == ["--progress"]


def test_discover_plan_progress_prints_no_progress_lines_but_its_command_carries_it(
    tmp_path, monkeypatch
):
    stops = install_driven_ticker(monkeypatch)
    client = HookedHub(search_results=[summary(REPO_ID)])
    install_hub(monkeypatch, client)

    result = discover(new_archive(tmp_path), "--plan", "--progress", stdin="1\n0\n1\n*Q4_K_M*\n")

    assert result.exit_code == 0, click.unstyle(result.output)
    hints = lines_with(result, RESUME)
    assert len(hints) == 1
    assert tail(hints[0], 1) == ["--progress"]
    assert progress_lines(lines_of(result)) == []
    assert client.download_calls == []
    assert stops == []


def test_match_mode_refuses_progress_at_exit_2(monkeypatch):
    # Match mode never pulls, so a swallowed --progress would read as a
    # pull that reported nothing — refused like --plan (review 2026-07-31).
    client = HookedHub(search_results=[])
    install_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3", "--progress"])

    out = click.unstyle(result.output)
    assert result.exit_code == 2
    assert "No such option" not in out  # the flag exists; this is the refusal
    assert "--progress" in out and "--match-ollama" in out
    assert client.search_calls == []
