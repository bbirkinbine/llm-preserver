"""Tests for spec 0008 — the --hf-logging flag.

Pins the contract: ``setup_logging(verbose, hf_logging=...)`` sets
``RUST_LOG=info`` (only when absent) and raises the ``huggingface_hub``
logger to exactly INFO — never DEBUG — while the default path stays
byte-identical to today. CLI surface: ``pull`` and ``discover`` both
accept ``--hf-logging``. No network; output is ``click.unstyle``d
before substring asserts (rich ANSI in CI).
"""

import contextlib
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import Result
from huggingface_hub.utils import logging as hf_hub_logging
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.cli.pull_exec import setup_logging
from llm_preserver.cli.resume_hint import compose_resume_hint

runner = CliRunner()


def combined_output(result: Result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def unstyled_output(result: Result) -> str:
    """Combined output with rich's ANSI styling stripped."""
    return click.unstyle(combined_output(result))


@pytest.fixture(autouse=True)
def _restore_hf_verbosity() -> Iterator[None]:
    """Snapshot and restore huggingface_hub verbosity (process-global).

    The ``llm_preserver`` package logger is restored by the suite-wide
    autouse fixture in conftest.py; this one covers the vendor logger
    these tests deliberately mutate.
    """
    before = hf_hub_logging.get_verbosity()
    yield
    hf_hub_logging.set_verbosity(before)


@pytest.fixture(autouse=True)
def _restore_rust_log() -> Iterator[None]:
    """Snapshot and restore RUST_LOG (process-global).

    monkeypatch.delenv on an already-absent key records nothing to
    undo, so a RUST_LOG written *by the code under test* would survive
    teardown and leak into unrelated tests.
    """
    before = os.environ.get("RUST_LOG")
    yield
    if before is None:
        os.environ.pop("RUST_LOG", None)
    else:
        os.environ["RUST_LOG"] = before


# --- CLI surface -----------------------------------------------------------


def test_pull_help_mentions_hf_logging() -> None:
    """pull --help lists the --hf-logging option."""
    result = runner.invoke(app, ["pull", "--help"])

    assert result.exit_code == 0
    assert "--hf-logging" in unstyled_output(result)


def test_discover_help_mentions_hf_logging() -> None:
    """discover --help lists the --hf-logging option."""
    result = runner.invoke(app, ["discover", "--help"])

    assert result.exit_code == 0
    assert "--hf-logging" in unstyled_output(result)


# --- setup_logging unit behavior -------------------------------------------


def test_sets_rust_log_info_when_env_var_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """hf_logging=True sets RUST_LOG=info when the variable is unset."""
    monkeypatch.delenv("RUST_LOG", raising=False)

    setup_logging(False, hf_logging=True)

    assert os.environ["RUST_LOG"] == "info"


def test_leaves_preexisting_rust_log_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user-set RUST_LOG filter wins over the flag's default."""
    monkeypatch.setenv("RUST_LOG", "warn")

    setup_logging(False, hf_logging=True)

    assert os.environ["RUST_LOG"] == "warn"


def test_raises_huggingface_hub_verbosity_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """hf_logging=True raises the huggingface_hub logger to INFO."""
    monkeypatch.delenv("RUST_LOG", raising=False)
    hf_hub_logging.set_verbosity(logging.WARNING)

    setup_logging(False, hf_logging=True)

    assert hf_hub_logging.get_verbosity() == logging.INFO


def test_flag_off_leaves_rust_log_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the flag, no environment variable is written."""
    monkeypatch.delenv("RUST_LOG", raising=False)

    setup_logging(False)

    assert "RUST_LOG" not in os.environ


def test_flag_off_leaves_huggingface_hub_verbosity_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the flag, the vendor logger keeps its prior verbosity."""
    monkeypatch.delenv("RUST_LOG", raising=False)
    hf_hub_logging.set_verbosity(logging.WARNING)

    setup_logging(False)

    assert hf_hub_logging.get_verbosity() == logging.WARNING


def test_verbose_with_hf_logging_pins_vendor_logger_at_info_not_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--verbose --hf-logging together never push huggingface_hub past INFO.

    Debug-level client logging emits request URLs / cURL equivalents
    (auth-adjacent) and must stay out of reach of any flag (spec 0008).
    """
    monkeypatch.delenv("RUST_LOG", raising=False)
    hf_hub_logging.set_verbosity(logging.WARNING)

    setup_logging(True, hf_logging=True)

    assert hf_hub_logging.get_verbosity() == logging.INFO
    assert logging.getLogger("llm_preserver").level == logging.DEBUG


def test_notice_when_inherited_rust_log_overrides_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An inherited RUST_LOG wins — and the flag says so, naming the value.

    Without the notice an inherited (or accidentally empty) RUST_LOG
    silences the Xet layer and the defeated flag reads as broken
    (adjudicated 2026-07-13).
    """
    monkeypatch.setenv("RUST_LOG", "warn")

    setup_logging(False, hf_logging=True)

    err = capsys.readouterr().err
    assert "RUST_LOG" in err
    assert "'warn'" in err
    assert "--hf-logging" in err


def test_no_notice_when_rust_log_was_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the flag's own value applies, the inherited notice stays silent."""
    monkeypatch.delenv("RUST_LOG", raising=False)

    setup_logging(False, hf_logging=True)

    assert "RUST_LOG" not in capsys.readouterr().err


def test_activation_line_when_flag_is_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The flag announces itself once: telemetry on, healthy transfers silent.

    Healthy transfers produce zero vendor output at info (verified
    live, spec 0008), so without this line the user cannot tell
    "working and healthy" from "not working".
    """
    monkeypatch.delenv("RUST_LOG", raising=False)

    setup_logging(False, hf_logging=True)

    err = capsys.readouterr().err
    assert "--hf-logging active" in err
    assert "silent" in err


def test_no_activation_line_without_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default path stays byte-identical: no activation line."""
    monkeypatch.delenv("RUST_LOG", raising=False)

    setup_logging(False)

    assert "--hf-logging" not in capsys.readouterr().err


def test_inherited_notice_replaces_the_activation_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With an inherited RUST_LOG the notice speaks for the Xet half — once.

    Two startup lines about the same flag would be noise; the notice
    carries the activation role in this branch (spec 0008).
    """
    monkeypatch.setenv("RUST_LOG", "warn")

    setup_logging(False, hf_logging=True)

    err = capsys.readouterr().err
    assert "inherited RUST_LOG" in err
    assert "--hf-logging active" not in err


def test_hf_xet_is_not_imported_at_cli_startup() -> None:
    """The Xet runtime must load lazily, at first transfer — never at startup.

    --hf-logging writes RUST_LOG in the command callback; hf_xet reads
    it on its own initialization. The flag therefore only works while
    hf_xet's import stays lazy (inside huggingface_hub's download
    path). An eager ``import hf_xet`` anywhere in this package or its
    startup imports would make the read happen before the flag's
    write and silently kill the Xet telemetry (verified live
    2026-07-13; recorded in spec 0008).
    """
    import subprocess
    import sys

    code = "import llm_preserver.cli, sys; sys.exit(1 if 'hf_xet' in sys.modules else 0)"
    # S603: argv is sys.executable plus a hard-coded literal — nothing untrusted.
    proc = subprocess.run([sys.executable, "-c", code], check=False)  # noqa: S603

    assert proc.returncode == 0, "hf_xet was imported at CLI startup; --hf-logging is broken"


# --- resume-hint composition -------------------------------------------------


def test_resume_hint_carries_hf_logging_only_when_enabled(tmp_path: Path) -> None:
    """The hint replays --hf-logging when the pull ran with it — never otherwise.

    The flag exists for the stalled-transfer scenario the resume hint
    serves, so the continue command must not silently drop it
    (adjudicated 2026-07-13).
    """
    with_flag = compose_resume_hint(
        "acme/tiny-chat", tmp_path, include=["*Q4*"], model="acme/tiny-chat", hf_logging=True
    )
    without_flag = compose_resume_hint(
        "acme/tiny-chat", tmp_path, include=["*Q4*"], model="acme/tiny-chat"
    )

    assert with_flag is not None and "--hf-logging" in with_flag
    assert without_flag is not None and "--hf-logging" not in without_flag


# --- CLI wiring -------------------------------------------------------------


def test_pull_hf_logging_flag_sets_rust_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pull --hf-logging applies RUST_LOG at CLI startup.

    The archive path deliberately does not exist: the command fails
    fast after setup_logging runs, before any hub-client construction,
    so no fake hub is needed — only the env mutation is asserted.
    """
    monkeypatch.delenv("RUST_LOG", raising=False)

    result = runner.invoke(
        app,
        [
            "pull",
            "bartowski/tiny-chat-GGUF",
            str(tmp_path / "no-archive-here"),
            "--include",
            "*Q4_K_M*",
            "--hf-logging",
            "--yes",
        ],
    )

    assert result.exit_code != 0  # bad archive path still fails as today
    assert os.environ.get("RUST_LOG") == "info"


def test_discover_hf_logging_flag_sets_rust_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """discover --hf-logging applies RUST_LOG at CLI startup.

    Same fail-fast shape as the pull wiring test: the nonexistent
    archive path stops the command right after setup_logging, so the
    env mutation is observable without a fake hub.
    """
    monkeypatch.delenv("RUST_LOG", raising=False)

    result = runner.invoke(
        app,
        [
            "discover",
            "tiny chat",
            str(tmp_path / "no-archive-here"),
            "--hf-logging",
        ],
    )

    assert result.exit_code != 0  # bad archive path still fails as today
    assert os.environ.get("RUST_LOG") == "info"
