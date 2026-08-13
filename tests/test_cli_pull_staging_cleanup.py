"""Spec 0019 at the CLI: exit code, wording, and the verify interplay.

The observable half of the bug: a pull that archived every byte and
wrote its record exits **3** with ``error [local environment]`` because
deleting hf's bookkeeping raised. Spec 0019 makes that run exit 0.

What it deliberately does *not* do is clear the leftover: the drafted
no-op clear was cut at review, so residue outlives the pull that made
it until a human removes it. Two tests pin that decision rather than
the convenience it replaced, because an automatic clear cannot tell a
concurrent pull's live leaf from dead residue.

``verify --staging`` is a non-goal: its counting rule (the whole leaf,
hf ``.cache/`` bookkeeping included, adjudicated in spec 0012) must not
move, so the last test here is a green-now regression pin, not a red
one.

Everything runs inside ``tmp_path`` via ``typer.testing.CliRunner``
with the hub-client seam faked; no network. Output is unstyled before
any substring assert (rich-ANSI-in-CI rule).
"""

import contextlib
import json
import logging
import shutil
from pathlib import Path

import click
import pytest
from staging_shapes import (
    Q4_NAME,
    Q8_NAME,
    REPO_ID,
    failing_rmtree,
    model_dir,
    staging_leaf,
    write_bookkeeping,
)
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

PULL_ARGS = ("--include", "*Q4_K_M*")


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


def reset_cli_logging() -> None:
    """Drop the package log handler between two CliRunner invocations.

    ``setup_logging`` installs a ``StreamHandler`` bound to the stderr
    that was current when it ran, and only installs one. Left in place,
    a second ``invoke`` writes its log lines into the first
    invocation's closed stream instead of its own captured output.
    """
    package_logger = logging.getLogger("llm_preserver")
    package_logger.handlers.clear()


def init_archive_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch: pytest.MonkeyPatch, client) -> None:
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke_pull(archive: Path, *extra: str):
    reset_cli_logging()
    return runner.invoke(app, ["pull", REPO_ID, str(archive), *PULL_ARGS, *extra])


def break_cleanup(monkeypatch: pytest.MonkeyPatch, archive: Path) -> Path:
    """Make the staging-leaf removal raise the live ENOTEMPTY."""
    leaf = staging_leaf(archive)
    monkeypatch.setattr(shutil, "rmtree", failing_rmtree(leaf))
    return leaf


def test_cli_pull_exits_zero_when_the_staging_cleanup_fails(
    tmp_path, monkeypatch, fake_hub_factory
):
    """A complete, recorded pull reports success even if cleanup raised."""
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))
    break_cleanup(monkeypatch, archive)

    result = invoke_pull(archive, "--yes")

    assert result.exit_code == 0
    assert (archive / "models" / "bartowski" / "tiny-chat-GGUF" / "model-record.json").is_file()


def test_cli_pull_cleanup_failure_prints_no_fault_domain_error(
    tmp_path, monkeypatch, fake_hub_factory
):
    """No ``error [local environment]`` line: nothing about the pull failed."""
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))
    break_cleanup(monkeypatch, archive)

    result = invoke_pull(archive, "--yes")

    out = output_of(result)
    assert "error [local environment]" not in out
    assert "local filesystem failure during pull" not in out


def test_cli_pull_cleanup_failure_still_reports_the_pull_landed(
    tmp_path, monkeypatch, fake_hub_factory
):
    """The human sees the success line naming where the model landed."""
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))
    leaf = break_cleanup(monkeypatch, archive)

    result = invoke_pull(archive, "--yes")

    out = output_of(result)
    assert f"pulled {REPO_ID} into" in out
    assert str(leaf) in out  # the warning naming the leaf reaches the terminal


def test_a_repull_does_not_clear_leftover_residue(tmp_path, monkeypatch, fake_hub_factory):
    """A re-pull is deliberately NOT the retry — residue outlives it.

    The drafted no-op clear was cut at review: huggingface_hub writes
    its ``.cache/`` scaffolding before the first network call, so a
    concurrent pull's live staging leaf is indistinguishable from dead
    residue, and clearing it killed that pull. Residue therefore stays
    until a human removes it. Pinned so the race cannot be
    reintroduced by accident — the docs and the warning both promise
    exactly this, and a silent automatic clear would make them lie.
    """
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))
    assert invoke_pull(archive, "--yes").exit_code == 0
    leaf = write_bookkeeping(staging_leaf(archive), Q4_NAME)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))

    repull = invoke_pull(archive)
    reset_cli_logging()
    verified = runner.invoke(app, ["verify", str(archive), "--staging"])

    assert repull.exit_code == 0
    assert leaf.is_dir()  # untouched by the no-op re-pull
    assert REPO_ID in output_of(verified)  # and still reported


def test_an_adopt_only_pull_never_deletes_the_staging_leaf(tmp_path, monkeypatch, fake_hub_factory):
    """The ``to_download`` gate: a pull that moved no bytes deletes nothing.

    An adopt-only pull (files already on disk, record catching up)
    never filled the leaf, so it has no standing to remove one — which
    may hold a differently-scoped pull's staged bytes. Without this the
    gate at ``pull.py`` can be deleted with the whole suite green.
    """
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))
    assert invoke_pull(archive, "--yes").exit_code == 0
    # Un-record the weight while leaving it on disk: the next pull
    # adopts it (its hash matches the hub) and downloads nothing. The
    # doc sidecars must STAY recorded — the hub publishes no hash for
    # them, so an unrecorded doc is an integrity stop that would end
    # the pull before it ever reaches the cleanup this test is about.
    record_path = model_dir(archive) / "model-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for artifact in record["artifacts"]:
        artifact["files"] = [f for f in artifact["files"] if not f["path"].endswith(".gguf")]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    parked = staging_leaf(archive) / Q8_NAME
    parked.parent.mkdir(parents=True, exist_ok=True)
    parked.write_bytes(b"another subset's staged bytes")
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))

    result = invoke_pull(archive)

    assert result.exit_code == 0, output_of(result)
    assert parked.read_bytes() == b"another subset's staged bytes"


def test_verify_staging_still_counts_hf_bookkeeping_in_a_kept_leaf(
    tmp_path, monkeypatch, fake_hub_factory
):
    """Non-goal pin (green now): spec 0012's counting rule does not move.

    A leaf holding a genuinely interrupted download is left alone, and
    ``verify --staging`` keeps reporting the whole leaf — the hf
    ``.cache/`` sidecars included — so a large file whose only bytes
    live in ``*.incomplete`` can still never hide.
    """
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))
    assert invoke_pull(archive, "--yes").exit_code == 0
    leaf = write_bookkeeping(staging_leaf(archive), Q4_NAME)  # 2 files, 124 bytes
    partial = leaf / ".cache" / "huggingface" / "download" / f"{Q8_NAME}.incomplete"
    partial.write_bytes(b"z" * 4096)
    install_fake_hub(monkeypatch, fake_hub_factory(base_model=None))

    assert invoke_pull(archive).exit_code == 0
    reset_cli_logging()
    verified = runner.invoke(app, ["verify", str(archive), "--staging"])

    out = output_of(verified)
    assert REPO_ID in out
    assert "3 partial files" in out  # .lock + .metadata + .incomplete, all counted
