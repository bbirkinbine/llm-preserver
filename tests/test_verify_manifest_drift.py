"""A stale sidecar is reported, not silently overwritten.

``manifest-sha256.txt`` is derived from the record, so a full verify
regenerates it every run (spec 0009). That is correct — but it meant a
sidecar that disagreed with the record was replaced with no word to the
human, and the disagreement is exactly what matters: it is the state in
which ``sha256sum -c``, the offline check ADR 0001 rests its durability
claim on, would have failed.

Raised by Brian, 2026-08-11: "isn't verify supposed to verify?" The
answer is that regenerating derived output is fine and staying quiet
about having done it is not.
"""

import hashlib
from pathlib import Path

import click
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.records import MANIFEST_FILENAME

runner = CliRunner()

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"


def build(tmp_path: Path, write_model, sample_record_dict) -> Path:
    from llm_preserver.archive import init_archive

    archive = tmp_path / "archive"
    init_archive(archive)
    record = sample_record_dict(hub_id="acme/tiny-chat")
    record["artifacts"][0]["files"] = [
        {
            "path": PAYLOAD_REL,
            "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
            "size": len(PAYLOAD),
            "source": "original",
        }
    ]
    model_dir = write_model(archive, record)
    target = model_dir / PAYLOAD_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PAYLOAD)
    return archive


def test_a_stale_sidecar_is_reported(tmp_path, write_model, sample_record_dict) -> None:
    archive = build(tmp_path, write_model, sample_record_dict)
    runner.invoke(app, ["verify", str(archive)])  # write a correct manifest
    manifest = archive / "models" / "acme" / "tiny-chat" / MANIFEST_FILENAME
    manifest.write_text("0" * 64 + "  model-record.json\n", encoding="utf-8")

    result = runner.invoke(app, ["verify", str(archive)])

    out = click.unstyle(result.output)
    assert "manifest" in out.lower()
    assert "regenerated" in out.lower() or "stale" in out.lower()


def test_a_matching_sidecar_says_nothing(tmp_path, write_model, sample_record_dict) -> None:
    # The guard: a routine audit must not grow a line per model.
    archive = build(tmp_path, write_model, sample_record_dict)
    runner.invoke(app, ["verify", str(archive)])

    out = click.unstyle(runner.invoke(app, ["verify", str(archive)]).output)

    assert "regenerated" not in out.lower()


def test_a_stale_sidecar_does_not_change_the_verdict(
    tmp_path, write_model, sample_record_dict
) -> None:
    # Informational, like `unrecorded`: the payload verdict stands and
    # the exit code is unchanged — the sidecar is regenerable output,
    # not evidence of damage.
    archive = build(tmp_path, write_model, sample_record_dict)
    runner.invoke(app, ["verify", str(archive)])
    manifest = archive / "models" / "acme" / "tiny-chat" / MANIFEST_FILENAME
    manifest.write_text("garbage\n", encoding="utf-8")

    result = runner.invoke(app, ["verify", str(archive)])

    assert result.exit_code == 0
    assert "valid" in click.unstyle(result.output)
