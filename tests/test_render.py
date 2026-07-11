"""Tests for llm_preserver.render — MODEL-RECORD.md generation.

The markdown is a generated rendering of the JSON record (ADR 0001):
it must warn readers to edit the JSON, and it is never parsed back.
"""

import datetime

from llm_preserver.records import ArtifactEntry, FileEntry, ModelRecord
from llm_preserver.render import render_model_record

FULL_COMMIT_HASH = "b" * 40


def make_record() -> ModelRecord:
    return ModelRecord(
        name="tiny-chat",
        hub_id="acme/tiny-chat",
        roles=["chat"],
        license="apache-2.0",
        parameter_count="1B",
        context_length=4096,
        notes=None,
        artifacts=[
            ArtifactEntry(
                format="gguf",
                quantization="Q4_K_M",
                source_repo="https://huggingface.co/other/tiny-chat-GGUF",
                revision=FULL_COMMIT_HASH,
                download_date=datetime.date(2026, 7, 9),
                runtime_tested="llama.cpp b4000",
                provenance="verified",
                files=[
                    FileEntry(
                        path="gguf/tiny-chat-Q4_K_M.gguf",
                        sha256="0" * 64,
                        size=12345,
                        source="original",
                    )
                ],
            )
        ],
    )


def test_header_warns_generated_and_points_at_json():
    markdown = render_model_record(make_record())
    header = "\n".join(markdown.splitlines()[:5]).lower()
    assert "generated" in header
    assert "model-record.json" in header


def test_markdown_includes_model_identity():
    markdown = render_model_record(make_record())
    assert "tiny-chat" in markdown
    assert "acme/tiny-chat" in markdown


def test_markdown_includes_artifact_details():
    markdown = render_model_record(make_record())
    assert "gguf" in markdown
    assert "Q4_K_M" in markdown
    assert FULL_COMMIT_HASH in markdown


def test_multiline_hub_value_cannot_forge_markdown_structure():
    # Record scalars are hub-derived; a value carrying newlines must
    # not inject headings (or any line structure) into the rendering.
    record = make_record().model_copy(update={"license": "apache-2.0\n\n## forged heading"})
    markdown = render_model_record(record)
    assert not any(line.startswith("## forged") for line in markdown.splitlines())
    assert "forged heading" in markdown  # content survives, structure doesn't


def test_control_characters_in_record_fields_are_stripped():
    # A hostile record must not be able to inject ANSI/OSC escapes
    # into terminal output — C0 (\x1b, \x07) and C1 (\x9d OSC, \x90
    # DCS) introducers alike.
    record = make_record().model_copy(update={"notes": "\x1b]0;pwned\x07\x9d\x90visible"})
    markdown = render_model_record(record)
    assert "\x1b" not in markdown
    assert "\x07" not in markdown
    assert "\x9d" not in markdown
    assert "\x90" not in markdown
    assert "visible" in markdown
