"""Discovery-side hub types: model summaries, relations, paging.

Spec 0006: the search and model-tree listing stages consume light
per-repo summaries, paged. This module holds the pure types and
normalization; the network calls live on ``HubClient`` (hub.py),
behind the same protocol seam the fakes implement.

Hub API facts encoded here were live-verified 2026-07-13 against
huggingface_hub 1.23.0 (see spec 0006 ``## External references``):
list responses carry ``downloads`` (int), ``last_modified``
(datetime), ``gated`` (False | "auto" | "manual"), and — with the
``baseModels`` expand — ``base_models`` as
``{"relation": ..., "models": [{"id": ...}]}``.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from typing import Literal

from llm_preserver.records import ID_COMPONENT_RE

PAGE_SIZE = 20


def looks_like_repo_id(value: str) -> bool:
    """True for a plausible hub repo id (one or two validated components).

    ``base_model`` is free-text hub metadata; only strings shaped
    like repo ids may become request parameters.
    """
    parts = value.split("/")
    return 1 <= len(parts) <= 2 and all(ID_COMPONENT_RE.fullmatch(part) for part in parts)


RelationType = Literal["quantized", "finetune", "adapter", "merge"]


@dataclass(frozen=True)
class ModelSummary:
    """One repo row in a discovery listing — hub facts, no judgment.

    Attributes:
        repo_id: The hub repo id (post-rename when the hub redirected).
        downloads: The hub's download count, or None when absent.
        last_modified: ISO-8601 timestamp string, or None.
        gated: None for open repos; the hub's gating mode ("auto" /
            "manual") when access requires accepted terms.
        base_model: First declared parent repo id, or None.
        relation: The model-tree relation this row was listed under,
            when known from the listing context.
    """

    repo_id: str
    downloads: int | None
    last_modified: str | None
    gated: str | None
    base_model: str | None
    relation: str | None = None


class HubPager:
    """Fixed-size pages over a lazy summary iterator.

    The hub API paginates by cursor with no total count, so the pager
    exposes only what is knowable: how many rows came back so far and
    whether the source has ended. A short page means the source ended
    with it; an exact-multiple source is only known exhausted after
    the following empty page.
    """

    def __init__(self, source: Iterator[ModelSummary], page_size: int = PAGE_SIZE) -> None:
        self._source = source
        self._page_size = page_size
        self._exhausted = False
        self._fetched = 0

    def next_page(self) -> list[ModelSummary]:
        """Return up to ``page_size`` summaries; ``[]`` once exhausted."""
        if self._exhausted:
            return []
        page = list(islice(self._source, self._page_size))
        self._fetched += len(page)
        if len(page) < self._page_size:
            self._exhausted = True
        return page

    @property
    def exhausted(self) -> bool:
        """True once the source has ended."""
        return self._exhausted

    @property
    def fetched(self) -> int:
        """Total rows returned across all pages so far."""
        return self._fetched


def summarize(model: object, relation: RelationType | None = None) -> ModelSummary:
    """Normalize a hub listing/info object into a ``ModelSummary``.

    Args:
        model: A ``huggingface_hub`` ``ModelInfo``-shaped object —
            typed ``object`` because the client library's type is not
            a stable import surface; only the live-verified
            attributes are read, defensively via ``getattr``.
        relation: The relation the row was listed under, if any.

    Returns:
        The normalized summary — ``gated`` False becomes None,
        datetimes become ISO strings, the first ``baseModels`` id is
        extracted, absent fields become None.
    """
    gated = getattr(model, "gated", None)
    last_modified = getattr(model, "last_modified", None)
    base_models = getattr(model, "base_models", None)
    base_id: str | None = None
    if isinstance(base_models, dict):
        declared = base_models.get("models") or []
        # Hub/author-supplied shape: anything unexpected yields None,
        # never an unmapped crash mid-listing.
        if declared and isinstance(declared[0], dict):
            candidate = declared[0].get("id")
            base_id = candidate if isinstance(candidate, str) else None
    return ModelSummary(
        repo_id=getattr(model, "id"),  # noqa: B009 — uniform defensive access
        downloads=getattr(model, "downloads", None),
        last_modified=last_modified.isoformat() if last_modified is not None else None,
        gated=gated if isinstance(gated, str) else None,
        base_model=base_id,
        relation=relation,
    )
