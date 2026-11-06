"""The in-memory hub double shared by the suite (moved out of conftest).

Plain module, no fixtures, like ``staging_shapes.py``: ``conftest.py``
imports ``FakeHubClient`` from here and its ``fake_hub_factory`` fixture
builds instances, so every existing test keeps working unchanged. Moved
for spec 0026 pass 4, which grew the double a ``cached_locally`` probe
and a ``progress`` sink on ``download``; conftest would otherwise pass
the 300-line cap.

``llm_preserver.hub`` is imported lazily inside method bodies, so
collection of unrelated tests never depends on the implementation
existing.
"""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

FULL_COMMIT_HASH = "a" * 40

DEFAULT_HUB_FILES = [
    # (repo-relative path, content bytes, is_lfs — LFS files carry a
    # hub-declared sha256; non-LFS files publish no hash)
    ("tiny-chat-Q4_K_M.gguf", b"q4 weight bytes", True),
    ("tiny-chat-Q8_0.gguf", b"q8 weight bytes", True),
    ("README.md", b"# tiny-chat quantized\n", False),
]

# Any: the sentinel stands in for any sink type in the download signature.
_NOT_PASSED: Any = object()
"""Sentinel default for ``download(progress=...)``: tells "not passed"
apart from an explicit ``progress=None`` (spec 0026 criterion 4 pins
that a pull without a meter passes no ``progress`` key at all)."""


class FakeHubClient:
    """In-memory double for the spec-0003 hub seam; zero network.

    Implements the same surface as ``llm_preserver.hub.HubClient``:
    ``repo_info(repo_id, revision=None)``, ``download(repo_id, filename,
    revision, dest_dir, progress=None)``, ``cached_locally(repo_id,
    filename, revision)``, and the spec-0006 discovery methods
    (``search_models``, ``list_children``, ``model_summary``, serving
    canned ``ModelSummary`` data through a real ``HubPager``). Serves
    bytes from memory, records every download call in
    ``download_calls`` (and the exact keyword set each call received in
    ``download_kwargs``), and leaves the same ``.cache/huggingface/``
    bookkeeping the real client writes into a local dir, so tests can
    assert it never reaches the archive. Per the seam contract, the
    client raises fault-domain ``Pull*Error`` exceptions (the real
    implementation maps ``huggingface_hub`` exceptions internally).

    Spec 0026 knobs: ``cached_paths`` names the files the local
    Hugging Face cache "holds" (default none); ``progress_chunks`` maps
    a path to the signed byte increments its download reports to a
    given sink (default: the whole file as one increment; ``[]`` models
    a reuse or cache copy that moved no network bytes);
    ``served_bytes`` maps a path to bytes written instead of the
    declared content (a corrupted transfer).
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
        search_results=None,
        children=None,
        summaries=None,
        search_error=None,
        list_children_error=None,
        model_summary_error=None,
        repos=None,
        repo_info_errors=None,
        cached_paths: Iterable[str] = (),
        progress_chunks: Mapping[str, Sequence[int]] | None = None,
        served_bytes: Mapping[str, bytes] | None = None,
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
        # Discovery (spec 0006): canned ModelSummary rows, verbatim
        # order; children keyed by (repo_id, relation); summaries keyed
        # by repo_id. Per-method injected errors raise at call time.
        self.search_results = list(search_results) if search_results is not None else []
        self.children = dict(children) if children is not None else {}
        self.summaries = dict(summaries) if summaries is not None else {}
        self.search_error = search_error
        self.list_children_error = list_children_error
        self.model_summary_error = model_summary_error
        # Match mode (spec 0013): per-repo file lists consulted before
        # the single-repo default, and per-repo repo_info errors. Both
        # optional, so existing tests keep the spec-0003 defaults.
        self.repos = dict(repos) if repos is not None else {}
        self.repo_info_errors = dict(repo_info_errors) if repo_info_errors is not None else {}
        # Spec 0026: local-cache probe, sink chunking, corrupt serving.
        self.cached_paths = set(cached_paths)
        self.progress_chunks = dict(progress_chunks) if progress_chunks is not None else {}
        self.served_bytes = dict(served_bytes) if served_bytes is not None else {}
        self.download_calls: list[str] = []
        self.download_kwargs: list[dict[str, Any]] = []  # Any: see download()
        self.cached_locally_calls: list[tuple[str, str, str]] = []
        self.repo_info_calls: list[str] = []
        # Spec 0024: the revision each repo_info / download call carried
        # (None = the default-branch call, made without the argument).
        self.repo_info_revisions: list[str | None] = []
        self.download_revisions: list[tuple[str, str]] = []
        self.search_calls: list[str] = []
        self.list_children_calls: list[tuple[str, str]] = []
        self.model_summary_calls: list[str] = []

    def repo_info(self, repo_id: str, revision: str | None = None):
        from llm_preserver.hub import RepoFile, RepoInfo

        self.repo_info_calls.append(repo_id)
        self.repo_info_revisions.append(revision)
        if repo_id in self.repo_info_errors:
            raise self.repo_info_errors[repo_id]
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
                for path, content, is_lfs in self.repos.get(repo_id, self.files)
            ],
            base_model=self.base_model,
            pipeline_tag=self.pipeline_tag,
            license=self.license,
        )

    def download(
        self,
        repo_id: str,
        filename: str,
        revision: str,
        dest_dir,
        progress=_NOT_PASSED,
    ) -> Path:
        # Any: values are the heterogeneous arguments the call received.
        call: dict[str, Any] = {
            "repo_id": repo_id,
            "filename": filename,
            "revision": revision,
            "dest_dir": dest_dir,
        }
        if progress is not _NOT_PASSED:
            call["progress"] = progress
        self.download_kwargs.append(call)
        self.download_revisions.append((filename, revision))
        if (
            self.download_error is not None
            and len(self.download_calls) >= self.fail_after_downloads
        ):
            self.download_calls.append(filename)
            raise self.download_error
        self.download_calls.append(filename)
        content = next(data for path, data, _is_lfs in self.files if path == filename)
        if progress is not _NOT_PASSED and progress is not None:
            for chunk in self.progress_chunks.get(filename, [len(content)]):
                progress.add_transfer_bytes(chunk)
        target = Path(dest_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.served_bytes.get(filename, content))
        bookkeeping = (
            Path(dest_dir) / ".cache" / "huggingface" / "download" / f"{filename}.metadata"
        )
        bookkeeping.parent.mkdir(parents=True, exist_ok=True)
        bookkeeping.write_text("etag bookkeeping\n", encoding="utf-8")
        return target

    def cached_locally(self, repo_id: str, filename: str, revision: str) -> bool:
        """Whether the fake local Hugging Face cache "holds" ``filename``."""
        self.cached_locally_calls.append((repo_id, filename, revision))
        return filename in self.cached_paths

    def search_models(self, query: str):
        """Serve the canned search results verbatim — never re-sorted."""
        from llm_preserver.hub_discovery import HubPager

        self.search_calls.append(query)
        if self.search_error is not None:
            raise self.search_error
        return HubPager(iter(self.search_results))

    def list_children(self, repo_id: str, relation: str):
        """Serve canned children for exactly ``(repo_id, relation)``."""
        from llm_preserver.hub_discovery import HubPager

        self.list_children_calls.append((repo_id, relation))
        if self.list_children_error is not None:
            raise self.list_children_error
        return HubPager(iter(self.children.get((repo_id, relation), [])))

    def model_summary(self, repo_id: str):
        """Serve the canned summary; an unknown repo is the 404 user fault."""
        from llm_preserver.hub import PullUserError

        self.model_summary_calls.append(repo_id)
        if self.model_summary_error is not None:
            raise self.model_summary_error
        if repo_id not in self.summaries:
            raise PullUserError(
                f"repo, revision, or file not found (or access not granted): {repo_id}"
            )
        return self.summaries[repo_id]
