"""The real Hugging Face client — the only module calling the library.

Isolating every client call behind this module localizes upstream API
churn to one seam (spec 0003, client-churn posture).

Auth is ambient: ``huggingface_hub`` discovers ``HF_TOKEN`` or the
``hf auth login`` token file on its own. This module passes no token
arguments, stores nothing, and never reads or logs the token value.

API facts (``model_info`` fields, ``hf_hub_download`` local-dir
behavior) verified against the installed ``huggingface_hub`` 1.23.0
and its official docs (Apache-2.0), retrieved 2026-07-10 — sources
pinned in ``docs/specs/0003-selective-pull.md`` → "External
references". Discovery listing facts (``expand`` fields, the
``base_model:<relation>:<id>`` filter, ``sort="downloads"``)
live-verified 2026-07-13 — sources pinned in spec 0006.
"""

from collections.abc import Iterator
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.hf_api import ExpandModelProperty_T

from llm_preserver.hub.errors import MAPPED_EXCEPTIONS, PullHubError, map_hub_exception
from llm_preserver.hub.types import RepoFile, RepoInfo
from llm_preserver.hub_discovery import HubPager, ModelSummary, RelationType, summarize

# Discovery listing fields (spec 0006, live-verified 2026-07-13).
_DISCOVERY_EXPAND: list[ExpandModelProperty_T] = [
    "downloads",
    "lastModified",
    "gated",
    "baseModels",
]


def _first_str(value: object) -> str | None:
    """Normalize a card-data field that may be a string or list of strings."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


class HubClient:
    """Hugging Face implementation of the hub seam.

    Downloads go through the official client (CDN redirects, retries,
    Xet backend, native within-call resume). Auth is ambient — no
    token arguments anywhere.
    """

    def __init__(self) -> None:
        self._api = HfApi()

    def repo_info(self, repo_id: str) -> RepoInfo:
        """Fetch repo metadata in one ``model_info`` call.

        ``files_metadata=True`` supplies file sizes for selection and
        hub-declared LFS SHA256s for ``verified`` provenance.

        Args:
            repo_id: Exact hub repo id (``namespace/repo``).

        Returns:
            The repo's files and record-relevant facts.

        Raises:
            PullError: The fault-domain mapping of any client failure.
        """
        try:
            info = self._api.model_info(repo_id, files_metadata=True)
        except MAPPED_EXCEPTIONS as exc:
            raise map_hub_exception(exc) from exc
        if info.sha is None:
            raise PullHubError(f"hub returned no commit hash for {repo_id}: retry later")
        card = info.card_data
        return RepoInfo(
            commit=info.sha,
            files=[
                RepoFile(
                    path=sibling.rfilename,
                    size=sibling.size,
                    sha256=sibling.lfs.sha256 if sibling.lfs is not None else None,
                )
                for sibling in info.siblings or []
            ],
            base_model=_first_str(card.base_model) if card is not None else None,
            pipeline_tag=info.pipeline_tag,
            license=_first_str(card.license) if card is not None else None,
        )

    def download(self, repo_id: str, filename: str, revision: str, dest_dir: Path) -> Path:
        """Download one file at a pinned revision into a staging dir.

        Uses the client's ``local_dir`` mode, which leaves
        ``.cache/huggingface/`` bookkeeping in ``dest_dir``; the caller
        stages, hashes, and moves files, then discards the bookkeeping
        (it must never reach the archive).

        Args:
            repo_id: Exact hub repo id (``namespace/repo``).
            filename: Repo-relative file to fetch.
            revision: The pinned commit hash (never a branch name).
            dest_dir: Staging directory to download into.

        Returns:
            The path of the downloaded file inside ``dest_dir``.

        Raises:
            PullError: The fault-domain mapping of any client failure.
        """
        try:
            local = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                local_dir=dest_dir,
            )
        except MAPPED_EXCEPTIONS as exc:
            raise map_hub_exception(exc) from exc
        return Path(local)

    def _summaries(
        self, source: Iterator[object], relation: RelationType | None = None
    ) -> Iterator[ModelSummary]:
        """Yield normalized summaries, mapping lazy-iteration failures.

        ``list_models`` is a cursor iterator — HTTP fires during
        iteration, so the fault-domain mapping must wrap the loop,
        not just the call.
        """
        try:
            for model in source:
                yield summarize(model, relation)
        except MAPPED_EXCEPTIONS as exc:
            raise map_hub_exception(exc) from exc

    def search_models(self, query: str) -> HubPager:
        """Free-text hub search — the hub's relevance order, verbatim.

        No ``sort`` is requested: re-ordering is the hub's judgment or
        nobody's (spec 0006 no-ranking invariant).
        """
        source = self._api.list_models(search=query, expand=_DISCOVERY_EXPAND)
        return HubPager(self._summaries(source))

    def list_children(self, repo_id: str, relation: RelationType) -> HubPager:
        """One relation's model-tree children, hub-sorted by downloads.

        Ordering by a stated hub fact keeps the listing useful for
        popular bases (hundreds of children) without the tool ranking.
        """
        source = self._api.list_models(
            filter=f"base_model:{relation}:{repo_id}",
            sort="downloads",
            expand=_DISCOVERY_EXPAND,
        )
        return HubPager(self._summaries(source, relation))

    def model_summary(self, repo_id: str) -> ModelSummary:
        """Light single-repo facts for parent-chain hops.

        A renamed repo comes back under its post-rename id (the hub
        redirects); the caller compares against what it asked for.

        Raises:
            PullError: The fault-domain mapping of any client failure
                (a 404 is the user-input domain).
        """
        try:
            info = self._api.model_info(repo_id, expand=_DISCOVERY_EXPAND)
        except MAPPED_EXCEPTIONS as exc:
            raise map_hub_exception(exc) from exc
        return summarize(info)
