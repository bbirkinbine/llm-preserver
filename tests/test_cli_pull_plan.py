"""CLI behavior of ``pull --plan`` (spec 0005, phase 3).

``--plan`` runs everything a pull does up to the confirmation point,
prints the report, and exits without downloading weights or writing
anything under the archive root. Questions a real pull would ask
surface as ``would ask:`` lines instead of prompts; exit 0 means the
pull would proceed, exit 3 means the disk preflight would refuse. One
adjudicated exception: a tree shipping ``adapter_config.json`` gets
that small file fetched (throwaway temp dir, never the archive) so the adapter-base
advisory can name the follow-up pull. Everything runs via
typer.testing.CliRunner with the hub-client seam faked; no network.
"""

import contextlib
import hashlib
import json
import shutil
from collections import namedtuple

from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "bartowski/tiny-chat-GGUF"
# The conftest default repo: *Q4_K_M* selects the Q4 weight, README rides.
Q4_SIZE = len(b"q4 weight bytes")
README_SIZE = len(b"# tiny-chat quantized\n")
TOTAL_BYTES = Q4_SIZE + README_SIZE
PLAN_ARGS = ("--include", "*Q4_K_M*", "--model", "acme/tiny-chat", "--plan")

GEMMA_REPO_ID = "ggml-org/gemma-tiny-GGUF"
GEMMA_FILES = [
    ("gemma-tiny-Q4_K_M.gguf", b"q4 weight bytes", True),
    ("mmproj-F16.gguf", b"projector bytes", True),
    ("README.md", b"# gemma tiny\n", False),
]

ADAPTER_REPO_ID = "acme/tiny-adapter"
ADAPTER_CONFIG = json.dumps({"base_model_name_or_path": "acme/base-7b"}).encode()
ADAPTER_FILES = [
    ("adapter_model.safetensors", b"adapter weight bytes", True),
    ("adapter_config.json", ADAPTER_CONFIG, False),
]

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


def tree_snapshot(root) -> dict[str, str]:
    """Relative path -> sha256 for every file under root (write detector)."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def invoke_pull(archive, *args, repo=REPO_ID, stdin=None):
    return runner.invoke(app, ["pull", repo, str(archive), *args], input=stdin)


def test_plan_prints_report_and_leaves_archive_untouched(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)
    before = tree_snapshot(archive)

    result = invoke_pull(archive, *PLAN_ARGS)

    assert result.exit_code == 0
    output = combined_output(result)
    lines = output.splitlines()
    assert any("tiny-chat-Q4_K_M.gguf" in line and str(Q4_SIZE) in line for line in lines)
    assert any("README.md" in line and str(README_SIZE) in line for line in lines)
    assert f"{TOTAL_BYTES} B" in output  # the total
    assert str(archive / "models" / "acme" / "tiny-chat") in output
    assert REPO_ID in output
    assert "nothing downloaded" in output  # the closing line
    assert f"from {REPO_ID}?" not in output  # no size confirmation under --plan
    assert client.download_calls == []
    assert not (archive / ".staging").exists()
    assert tree_snapshot(archive) == before


def test_plan_marks_archived_files_and_zero_download_total(tmp_path, monkeypatch, fake_hub_factory):
    # Pull for real first; the plan for the same selection then shows
    # only already-archived skips and a zero-byte download total.
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, fake_hub_factory())
    real = invoke_pull(archive, *PLAN_ARGS[:-1], "--yes")  # drop --plan, add --yes
    assert real.exit_code == 0
    plan_client = fake_hub_factory()
    install_fake_hub(monkeypatch, plan_client)
    before = tree_snapshot(archive)

    result = invoke_pull(archive, *PLAN_ARGS)

    assert result.exit_code == 0
    output = combined_output(result)
    assert "already archived" in output
    assert "0 B" in output
    assert plan_client.download_calls == []
    assert tree_snapshot(archive) == before


def test_plan_composes_with_whole_repo(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--whole-repo", "--model", "acme/tiny-chat", "--plan")

    assert result.exit_code == 0
    output = combined_output(result)
    for hub_path in ("tiny-chat-Q4_K_M.gguf", "tiny-chat-Q8_0.gguf", "README.md"):
        assert hub_path in output  # the plan covers the whole tree
    assert client.download_calls == []


def test_plan_after_interactive_listing_selection(tmp_path, monkeypatch, fake_hub_factory):
    # No --include, no --whole-repo: the listing prompt still runs (it
    # is input, not confirmation), then the plan prints and exits.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--model", "acme/tiny-chat", "--plan", stdin="*Q4_K_M*\n")

    assert result.exit_code == 0
    output = combined_output(result)
    assert f"files in {REPO_ID}:" in output  # the listing still printed
    assert f"{TOTAL_BYTES} B" in output  # then the plan did
    assert client.download_calls == []


def test_plan_surfaces_grouping_question_as_would_ask_line(tmp_path, monkeypatch, fake_hub_factory):
    # Without --model the conftest repo (base_model=acme/tiny-chat, gguf
    # tree) would prompt to group; --plan resolves it and reports it.
    # No stdin: an accidental real prompt would abort with exit 2.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--include", "*Q4_K_M*", "--plan")

    assert result.exit_code == 0
    output = combined_output(result)
    assert "would ask:" in output
    assert f"group {REPO_ID} under canonical model acme/tiny-chat?" in output
    assert client.download_calls == []


def test_plan_surfaces_every_weight_question_as_would_ask_line(
    tmp_path, monkeypatch, fake_hub_factory
):
    # *.gguf covers both quants — a real pull would ask the every-weight
    # question; --plan reports it and proceeds without a prompt.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--include", "*.gguf", "--model", "acme/tiny-chat", "--plan")

    assert result.exit_code == 0
    output = combined_output(result)
    assert "would ask:" in output
    assert f"selection covers every weight file in {REPO_ID}" in output
    assert client.download_calls == []


def test_plan_over_disk_budget_exits_3_stating_sizes(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: DiskUsage(10**12, 10**12 - 1, 1))
    before = tree_snapshot(archive)

    result = invoke_pull(archive, *PLAN_ARGS)

    assert result.exit_code == 3  # local-environment fault domain
    output = combined_output(result)
    assert "error [local environment]" in output
    assert f"{TOTAL_BYTES} B" in output  # needed
    assert "1 B" in output  # available
    assert client.download_calls == []
    assert tree_snapshot(archive) == before


def test_plan_names_exactly_what_the_real_pull_downloads(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    plan_client = fake_hub_factory()
    install_fake_hub(monkeypatch, plan_client)

    planned = invoke_pull(archive, *PLAN_ARGS)

    assert planned.exit_code == 0
    plan_output = combined_output(planned)
    assert "tiny-chat-Q4_K_M.gguf" in plan_output
    assert "README.md" in plan_output
    assert plan_client.download_calls == []

    real_client = fake_hub_factory()
    install_fake_hub(monkeypatch, real_client)
    real = invoke_pull(archive, *PLAN_ARGS[:-1], "--yes")

    assert real.exit_code == 0
    assert set(real_client.download_calls) == {"tiny-chat-Q4_K_M.gguf", "README.md"}


def test_plan_includes_companion_advisories(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(files=GEMMA_FILES, repo_id=GEMMA_REPO_ID, base_model=None)
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(
        archive, *PLAN_ARGS[:2], "--model", "acme/gemma-tiny", "--plan", repo=GEMMA_REPO_ID
    )

    assert result.exit_code == 0
    output = combined_output(result)
    assert "vision projector" in output
    assert "mmproj-F16.gguf" in output
    assert client.download_calls == []


def test_plan_with_yes_is_allowed_and_downloads_nothing(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory()
    install_fake_hub(monkeypatch, client)
    before = tree_snapshot(archive)

    result = invoke_pull(archive, *PLAN_ARGS, "--yes")

    assert result.exit_code == 0
    assert client.download_calls == []
    assert tree_snapshot(archive) == before


def test_plan_fetches_adapter_config_for_advisory_only(tmp_path, monkeypatch, fake_hub_factory):
    # The adjudicated exception: the plan downloads adapter_config.json
    # (and nothing else) to name the base model, says so out loud, and
    # still leaves the archive byte-identical — staging included.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(files=ADAPTER_FILES, repo_id=ADAPTER_REPO_ID, base_model=None)
    install_fake_hub(monkeypatch, client)
    before = tree_snapshot(archive)

    result = invoke_pull(
        archive,
        "--include",
        "*adapter_model*",
        "--model",
        "acme/tiny-adapter",
        "--plan",
        repo=ADAPTER_REPO_ID,
    )

    assert result.exit_code == 0
    output = combined_output(result)
    assert "adapter_config.json" in output
    assert "advisory only" in output  # the fetch is stated explicitly
    assert "llm-preserver pull acme/base-7b" in output  # the follow-up
    assert client.download_calls == ["adapter_config.json"]  # never weights
    assert not (archive / ".staging").exists()
    assert tree_snapshot(archive) == before
