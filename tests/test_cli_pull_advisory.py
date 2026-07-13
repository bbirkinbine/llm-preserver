"""CLI wiring of the companion-artifact advisory (spec 0005, phase 2).

A real (non-`--plan`) selective pull over a gemma-shaped fake repo must
print the mmproj advisory before the size confirmation, and the
advisory must never change what downloads. Everything runs via
typer.testing.CliRunner with the hub-client seam faked; no network.
"""

import contextlib

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "ggml-org/gemma-tiny-GGUF"
# The gemma incident shape: a quant, the vision projector, a doc file.
GEMMA_FILES = [
    ("gemma-tiny-Q4_K_M.gguf", b"q4 weight bytes", True),
    ("mmproj-F16.gguf", b"projector bytes", True),
    ("README.md", b"# gemma tiny\n", False),
]


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


def invoke_q4_pull(archive, *extra_args, stdin=None):
    args = [
        "pull",
        REPO_ID,
        str(archive),
        "--include",
        "*Q4_K_M*",
        "--model",
        "acme/gemma-tiny",
        *extra_args,
    ]
    return runner.invoke(app, args, input=stdin)


def test_pull_prints_mmproj_advisory_before_size_confirmation(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    install_fake_hub(
        monkeypatch, fake_hub_factory(files=GEMMA_FILES, repo_id=REPO_ID, base_model=None)
    )

    result = invoke_q4_pull(archive, stdin="y\n")

    assert result.exit_code == 0
    output = combined_output(result)
    assert "vision projector" in output
    assert "mmproj-F16.gguf" in output
    assert "--include" in output
    # The advisory prints before the confirmation prompt (spec 0005:
    # interactive use gets the safety net before committing to bytes).
    confirm_marker = f"from {REPO_ID}?"
    assert confirm_marker in output
    assert output.index("mmproj-F16.gguf") < output.index(confirm_marker)


def test_advisory_never_changes_the_downloaded_file_set(tmp_path, monkeypatch, fake_hub_factory):
    # Advisory only, never auto-add: the selection downloads exactly the
    # Q4 weight plus the riding doc, with the projector left behind.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(files=GEMMA_FILES, repo_id=REPO_ID, base_model=None)
    install_fake_hub(monkeypatch, client)

    result = invoke_q4_pull(archive, "--yes")

    assert result.exit_code == 0
    assert "vision projector" in combined_output(result)
    assert set(client.download_calls) == {"gemma-tiny-Q4_K_M.gguf", "README.md"}
    model_dir = archive / "models/acme/gemma-tiny"
    assert (model_dir / "gguf/gemma-tiny-Q4_K_M.gguf").is_file()
    assert not (model_dir / "gguf/mmproj-F16.gguf").exists()


def test_pull_prints_full_precision_master_advisory_for_unarchived_base(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The conftest default repo declares base_model=acme/tiny-chat; a
    # fresh archive has never pulled it, so the cross-repo row fires
    # with the exact follow-up command.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())

    result = runner.invoke(
        app,
        [
            "pull",
            "bartowski/tiny-chat-GGUF",
            str(archive),
            "--include",
            "*Q4_K_M*",
            "--model",
            "acme/tiny-chat",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "llm-preserver pull acme/tiny-chat --whole-repo" in combined_output(result)
