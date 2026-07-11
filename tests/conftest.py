"""Shared fixtures for the llm-preserver test suite.

These fixtures are pure data / filesystem helpers on purpose: they do
not import ``llm_preserver`` submodules at module level (the hub fakes
import ``llm_preserver.hub`` lazily inside method bodies), so
collection of unrelated tests never depends on the implementation
existing.
"""

import hashlib
import json
import logging
from pathlib import Path

import pytest

FULL_COMMIT_HASH = "a" * 40
FILE_SHA256 = "0" * 64
GGUF_FILE_SIZE = 12345

DEFAULT_HUB_FILES = [
    # (repo-relative path, content bytes, is_lfs — LFS files carry a
    # hub-declared sha256; non-LFS files publish no hash)
    ("tiny-chat-Q4_K_M.gguf", b"q4 weight bytes", True),
    ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
    ("README.md", b"# tiny-chat quantized\n", False),
]


class FakeHubClient:
    """In-memory double for the spec-0003 hub seam; zero network.

    Implements the same surface as ``llm_preserver.hub.HubClient``:
    ``repo_info(repo_id)`` and ``download(repo_id, filename, revision,
    dest_dir)``. Serves bytes from memory, records every download call
    in ``download_calls``, and leaves the same ``.cache/huggingface/``
    bookkeeping the real client writes into a local dir, so tests can
    assert it never reaches the archive. Per the seam contract, the
    client raises fault-domain ``Pull*Error`` exceptions (the real
    implementation maps ``huggingface_hub`` exceptions internally).

    ``llm_preserver.hub`` is imported lazily inside methods so that
    collection of unrelated tests never depends on the implementation
    existing.
    """

    def __init__(
        self,
        *,
        files,
        repo_id="bartowski/tiny-chat-GGUF",
        commit=FULL_COMMIT_HASH,
        base_model="acme/tiny-chat",
        pipeline_tag="text-generation",
        license="apache-2.0",
        repo_info_error=None,
        download_error=None,
        fail_after_downloads=0,
    ):
        self.files = list(files)
        self.repo_id = repo_id
        self.commit = commit
        self.base_model = base_model
        self.pipeline_tag = pipeline_tag
        self.license = license
        self.repo_info_error = repo_info_error
        self.download_error = download_error
        self.fail_after_downloads = fail_after_downloads
        self.download_calls: list[str] = []

    def repo_info(self, repo_id: str):
        from llm_preserver.hub import RepoFile, RepoInfo

        if self.repo_info_error is not None:
            raise self.repo_info_error
        return RepoInfo(
            commit=self.commit,
            files=[
                RepoFile(
                    path=path,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest() if is_lfs else None,
                )
                for path, content, is_lfs in self.files
            ],
            base_model=self.base_model,
            pipeline_tag=self.pipeline_tag,
            license=self.license,
        )

    def download(self, repo_id: str, filename: str, revision: str, dest_dir) -> Path:
        if (
            self.download_error is not None
            and len(self.download_calls) >= self.fail_after_downloads
        ):
            self.download_calls.append(filename)
            raise self.download_error
        self.download_calls.append(filename)
        content = next(data for path, data, _is_lfs in self.files if path == filename)
        target = Path(dest_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        bookkeeping = (
            Path(dest_dir) / ".cache" / "huggingface" / "download" / f"{filename}.metadata"
        )
        bookkeeping.parent.mkdir(parents=True, exist_ok=True)
        bookkeeping.write_text("etag bookkeeping\n", encoding="utf-8")
        return target


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
