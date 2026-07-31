"""Usable / not-usable display for the views breakdown (spec 0002).

Live-use adjudication 2026-07-31: usable models lead and are named,
not-usable models get one deduplicated reason, companion skips are
suppressed.
"""

import contextlib
import hashlib
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()


def output_of(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return click.unstyle(out)


def hexdigest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def file_entry(rel_path: str, sha256: str | None) -> dict[str, object]:
    return {"path": rel_path, "sha256": sha256, "size": 0, "source": "original"}


@pytest.fixture
def mixed_archive(
    tmp_path: Path,
    write_model: Callable[..., Path],
    sample_record_dict: Callable[..., dict],
) -> Path:
    """One usable model (GGUF + README companion), two snapshot-only."""
    archive = tmp_path / "archive"
    archive.mkdir()
    assert runner.invoke(app, ["init", str(archive)]).exit_code == 0

    def add(creator: str, model: str, fmt: str, entries: list[dict[str, object]]) -> None:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["format"] = fmt
        if fmt != "gguf":
            record["artifacts"][0]["quantization"] = None
        record["artifacts"][0]["files"] = entries
        model_dir = write_model(archive, record, creator=creator, model=model)
        for entry in entries:
            target = model_dir / str(entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")

    add(
        "acme",
        "tiny-chat",
        "gguf",
        [
            file_entry("gguf/tiny-chat-Q4_K_M.gguf", hexdigest("q4")),
            file_entry("gguf/docs/README.md", hexdigest("readme")),
        ],
    )
    add("acme", "snap-a", "hf-snapshot", [file_entry("model.safetensors", hexdigest("a"))])
    add("beta", "snap-b", "hf-snapshot", [file_entry("model.safetensors", hexdigest("b"))])
    return archive


def test_usable_models_are_named_and_companions_suppressed(
    tmp_path: Path, mixed_archive: Path
) -> None:
    result = runner.invoke(app, ["views", str(mixed_archive), "--dest", str(tmp_path / "view")])

    assert result.exit_code == 0
    out = output_of(result)
    assert "usable:" in out
    assert "acme/tiny-chat" in out.split("not usable:")[0]
    assert "README" not in out  # companion noise stays out of the display


def test_default_run_shows_complete_pasteable_flow(tmp_path: Path, mixed_archive: Path) -> None:
    """The dry run prints the full seed command and a real run name."""
    result = runner.invoke(app, ["views", str(mixed_archive), "--dest", str(tmp_path / "view")])

    out = output_of(result)
    assert "llm-preserver views" in out
    assert "--seed-store" in out
    assert "ollama run acme/tiny-chat:q4_k_m" in out  # first usable model's name


def test_seed_mode_lists_run_ready_minted_names(tmp_path: Path, mixed_archive: Path) -> None:
    result = runner.invoke(
        app, ["views", str(mixed_archive), "--dest", str(tmp_path / "view"), "--seed-store"]
    )

    assert result.exit_code == 0
    assert "acme/tiny-chat → acme/tiny-chat:q4_k_m" in output_of(result)


def test_not_usable_models_get_one_deduplicated_reason(tmp_path: Path, mixed_archive: Path) -> None:
    result = runner.invoke(app, ["views", str(mixed_archive), "--dest", str(tmp_path / "view")])

    out = output_of(result)
    # The section ends where the instructions begin — the run-example
    # there legitimately names the usable model.
    not_usable = out.split("not usable:")[1].split("Ollama cannot")[0]
    assert "acme/snap-a: safetensors snapshot only" in not_usable
    assert "beta/snap-b: safetensors snapshot only" in not_usable
    assert "acme/tiny-chat" not in not_usable


def test_problem_skips_still_surface_under_a_usable_model(
    tmp_path: Path,
    write_model: Callable[..., Path],
    sample_record_dict: Callable[..., dict],
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    assert runner.invoke(app, ["init", str(archive)]).exit_code == 0
    record = sample_record_dict(name="tiny-chat", hub_id="acme/tiny-chat")
    record["artifacts"][0]["files"] = [
        file_entry("gguf/tiny-chat-Q4_K_M.gguf", hexdigest("q4")),
        file_entry("gguf/tiny-chat-Q8_0.gguf", None),  # unhashed: a real problem
    ]
    model_dir = write_model(archive, record, creator="acme", model="tiny-chat")
    for rel in ("gguf/tiny-chat-Q4_K_M.gguf", "gguf/tiny-chat-Q8_0.gguf"):
        (model_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / rel).write_bytes(b"")

    result = runner.invoke(app, ["views", str(archive), "--dest", str(tmp_path / "view")])

    out = output_of(result)
    assert "unhashed" in out.split("usable:")[1]  # surfaced, not silent
