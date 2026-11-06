"""Shared fixtures for the llm-preserver test suite.

These fixtures are pure data / filesystem helpers on purpose: they do
not import ``llm_preserver`` submodules at module level (the hub fakes
import ``llm_preserver.hub`` lazily inside method bodies), so
collection of unrelated tests never depends on the implementation
existing. ``FakeHubClient`` lives in ``fake_hub.py`` and is re-exported
here, so ``from conftest import FakeHubClient`` keeps working.
"""

import json
import logging
from pathlib import Path

import pytest
from fake_hub import DEFAULT_HUB_FILES, FULL_COMMIT_HASH, FakeHubClient

FILE_SHA256 = "0" * 64
GGUF_FILE_SIZE = 12345


@pytest.fixture(autouse=True)
def _reset_package_logger():
    """Undo the CLI's package-logger setup after each test.

    ``pull --verbose`` attaches a handler to the ``llm_preserver``
    logger and disables propagation (so root-level DEBUG can never leak
    httpx/hub request logs). Left in place, that would starve caplog —
    which relies on propagation to the root handler — in later tests.
    """
    yield
    package_logger = logging.getLogger("llm_preserver")
    package_logger.handlers.clear()
    package_logger.propagate = True
    package_logger.setLevel(logging.NOTSET)


@pytest.fixture
def fake_hub_factory():
    """Factory for ``FakeHubClient`` instances with spec-0003 defaults.

    Keyword overrides pass straight through to ``FakeHubClient``; the
    default repo is a two-quant GGUF repo with a hash-less README.
    """

    def make(**overrides):
        overrides.setdefault("files", DEFAULT_HUB_FILES)
        return FakeHubClient(**overrides)

    return make


@pytest.fixture
def sample_record_dict():
    """Factory for a complete, valid model-record dict (JSON shape).

    Top-level fields can be overridden via keyword arguments.

    The artifact's ``source_repo`` follows ``hub_id`` on purpose: under
    ADR 0003 a model directory holds exactly one repo's bytes, so the
    default is a *migrated* record and `verify` reads it as ``valid``.
    Tests that need the pre-migration shape (an artifact from another
    repo) override ``source_repo`` explicitly — an unmigrated default
    would silently turn every verify-based test into a layout test.
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
                    "source_repo": "https://huggingface.co/acme/tiny-chat",
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
        if "artifacts" not in overrides:
            # Keep source_repo following an overridden hub_id, so a
            # helper that renames the model still produces a migrated
            # record. Without this, every caller passing hub_id= would
            # silently build the unmigrated shape and verify would read
            # it as a layout problem rather than whatever it was testing.
            for artifact in record["artifacts"]:
                artifact["source_repo"] = f"https://huggingface.co/{record['hub_id']}"
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
