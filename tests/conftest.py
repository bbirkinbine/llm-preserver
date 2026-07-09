"""Shared fixtures for the llm-preserver test suite.

These fixtures are pure data / filesystem helpers on purpose: they do
not import ``llm_preserver`` submodules, so collection of unrelated
tests never depends on the implementation existing.
"""

import json
from pathlib import Path

import pytest

FULL_COMMIT_HASH = "a" * 40
FILE_SHA256 = "0" * 64
GGUF_FILE_SIZE = 12345


@pytest.fixture
def sample_record_dict():
    """Factory for a complete, valid model-record dict (JSON shape).

    Top-level fields can be overridden via keyword arguments.
    """

    def make(**overrides):
        record = {
            "name": "tiny-chat",
            "hub_id": "acme/tiny-chat",
            "roles": ["chat"],
            "license": "apache-2.0",
            "parameter_count": "1B",
            "context_length": 4096,
            "notes": None,
            "artifacts": [
                {
                    "format": "gguf",
                    "quantization": "Q4_K_M",
                    "source_repo": "https://huggingface.co/other/tiny-chat-GGUF",
                    "revision": FULL_COMMIT_HASH,
                    "download_date": "2026-07-09",
                    "runtime_tested": "llama.cpp b4000",
                    "provenance": "verified",
                    "files": [
                        {
                            "path": "gguf/tiny-chat-Q4_K_M.gguf",
                            "sha256": FILE_SHA256,
                            "size": GGUF_FILE_SIZE,
                            "source": "original",
                        },
                    ],
                },
            ],
        }
        record.update(overrides)
        return record

    return make


@pytest.fixture
def write_model():
    """Factory that creates ``models/<creator>/<model>/`` in an archive.

    Writes ``model-record.json`` from the given dict; pass
    ``record=None`` to create a model dir with no record file. Payload
    files are deliberately never created — sizes must come from the
    record, not the disk.
    """

    def _write(
        archive_root: Path,
        record,
        creator: str = "acme",
        model: str = "tiny-chat",
    ) -> Path:
        model_dir = archive_root / "models" / creator / model
        model_dir.mkdir(parents=True, exist_ok=True)
        if record is not None:
            (model_dir / "model-record.json").write_text(json.dumps(record))
        return model_dir

    return _write
