"""CLI behavior of the selective-pull size confirmation (spec 0005).

The spec-0005 rider extends the whole-repo plan -> preflight -> confirm
sequence to selective pulls: `--include` and interactive pulls state
the selection's total download size in a confirmation before any bytes
move, `--yes` auto-accepts it (it starts with ``pull ``, the prefix
`_confirm_or_stop` keys on), declining it aborts with the user-input
exit (2) and downloads nothing, and an over-budget selection refuses
with the local-environment exit (3) before prompting. Everything runs
via typer.testing.CliRunner with the hub-client seam faked; no network.
"""

import contextlib
import shutil
from collections import namedtuple

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
# *Q4_K_M* selects the Q4 weight plus the README riding as a doc (the
# conftest default repo); the confirmed total is their byte lengths.
SELECTED_BYTES = len(b"q4 weight bytes") + len(b"# tiny-chat quantized\n")

DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke_pull(archive, *extra_args, stdin=None):
    args = [
        "pull",
        REPO_ID,
        str(archive),
        "--include",
        "*Q4_K_M*",
        "--model",
        "acme/tiny-chat",
        *extra_args,
    ]
    return runner.invoke(app, args, input=stdin)


def test_include_pull_confirms_size_before_pulling(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, stdin="y\n")

    assert result.exit_code == 0
    assert f"{SELECTED_BYTES} B" in combined_output(result)  # the stated total
    assert (archive / "models/acme/tiny-chat/gguf/tiny-chat-Q4_K_M.gguf").is_file()


def test_declined_size_confirmation_exits_2_downloading_nothing(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, stdin="n\n")

    assert result.exit_code == 2  # user-input fault domain
    assert list((archive / "models").iterdir()) == []


def test_unanswerable_selective_size_confirm_exits_2_naming_yes(
    tmp_path, monkeypatch, fake_hub_factory
):
    # No stdin and no --yes: the size confirmation cannot be answered,
    # so the CLI stops deterministically naming the bypass.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive)

    assert result.exit_code == 2
    assert "--yes" in combined_output(result)
    assert list((archive / "models").iterdir()) == []


def test_yes_auto_accepts_the_selective_size_confirmation(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = invoke_pull(archive, "--yes")  # no stdin needed at all

    assert result.exit_code == 0
    assert (archive / "models/acme/tiny-chat/gguf/tiny-chat-Q4_K_M.gguf").is_file()


def test_interactive_selection_declining_size_confirmation_downloads_nothing(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The listing flow gains the same confirmation after patterns are
    # entered: decline to walk away with nothing written.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        ["pull", REPO_ID, str(archive), "--model", "acme/tiny-chat"],
        input="*Q4_K_M*\nn\n",  # patterns, then decline the size confirm
    )

    assert result.exit_code == 2
    assert list((archive / "models").iterdir()) == []


def test_selective_pull_over_disk_budget_exits_3_without_prompting(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Plan -> preflight -> confirm: an over-budget selection refuses in
    # the local-environment domain before asking anyone to confirm it —
    # with no stdin, reaching a prompt would have been exit 2 instead.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())
    monkeypatch.setattr(shutil, "disk_usage", lambda path: DiskUsage(10**12, 10**12 - 1, 1))

    result = invoke_pull(archive)

    assert result.exit_code == 3  # local-environment fault domain
    assert "local environment" in combined_output(result)
    assert list((archive / "models").iterdir()) == []
