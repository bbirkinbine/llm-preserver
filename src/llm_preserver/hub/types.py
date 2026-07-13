"""Hub data types and the client protocol seam.

Pull orchestration and the CLI see only these types plus the
fault-domain errors — never ``huggingface_hub`` objects.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from llm_preserver.hub_discovery import HubPager, ModelSummary, RelationType


@dataclass(frozen=True)
class RepoFile:
    """One file in a hub repo, from the single metadata call.

    Attributes:
        path: Repo-relative filename, verbatim from the hub.
        size: Size in bytes, or None when the hub does not report one.
        sha256: Hub-declared SHA256 (LFS files only); None means the
            hub publishes no hash for this file.
    """

    path: str
    size: int | None
    sha256: str | None


@dataclass(frozen=True)
class RepoInfo:
    """Repo metadata needed for selection, grouping, and the record.

    Attributes:
        commit: The resolved commit hash the file list was read at —
            the pin every pull records (a branch name is a moving
            pointer, not provenance).
        files: Every file in the repo at that commit.
        base_model: The model card's ``base_model``, used to group a
            quant repo under its canonical model; None when absent.
        pipeline_tag: The repo's ``pipeline_tag``, recorded verbatim.
        license: The model card's license label, or None.
    """

    commit: str
    files: list[RepoFile]
    base_model: str | None
    pipeline_tag: str | None
    license: str | None


class HubClientProtocol(Protocol):
    """Structural seam between pull orchestration and hub access.

    Implementations raise the fault-domain ``Pull*Error`` exceptions;
    the real client maps ``huggingface_hub`` exceptions internally via
    ``map_hub_exception``.
    """

    def repo_info(self, repo_id: str) -> RepoInfo:
        """Fetch repo metadata (files with sizes/hashes, repo facts)."""
        ...

    def download(self, repo_id: str, filename: str, revision: str, dest_dir: Path) -> Path:
        """Download one file at ``revision`` into ``dest_dir``."""
        ...

    def search_models(self, query: str) -> HubPager:
        """Free-text hub search, the hub's relevance order, paged."""
        ...

    def list_children(self, repo_id: str, relation: RelationType) -> HubPager:
        """Model-tree children of one relation, hub-sorted by downloads."""
        ...

    def model_summary(self, repo_id: str) -> ModelSummary:
        """Light single-repo facts for parent-chain hops."""
        ...
