"""Grouping the shelf by declared lineage (spec 0017, criterion 7).

ADR 0003 took something away: under the old layout, ``ls`` showed every
form of one model in a single directory, and the path itself stated
that a quant derived from a model. One directory per source repo is
right for every other reason, but that structural statement has to come
back somewhere, or the archive is less legible than before rather than
more.

Here is where it comes back. The record carries the claim; this module
turns a flat inventory into the ordered, indented view ``status``
prints, including a header for a base the archive does *not* hold —
which is a fact worth seeing, not a gap to hide.

One level of indentation, deliberately: only a base that declares no
base of its own adopts, so a derivative of a derivative heads its own
group at the left margin rather than nesting two deep. No recursion
means no cycle to detect and no depth to bound.
"""

from dataclasses import dataclass

from llm_preserver.archive import ModelSummary


@dataclass(frozen=True)
class LineageRow:
    """One line of the grouped shelf.

    Attributes:
        summary: The model, or None for a placeholder header naming a
            base the archive does not hold.
        model_id: The id to print — the summary's, or the absent base's.
        depth: 0 for a base or an ungrouped model, 1 for a derivative.
        archived: False only for a placeholder header.
    """

    summary: ModelSummary | None
    model_id: str
    depth: int
    archived: bool = True


def group_by_lineage(summaries: list[ModelSummary]) -> list[LineageRow]:
    """Order the shelf so derivatives sit under the base they declare.

    Every model appears **exactly once**. A model is indented under its
    declared base when that base declares no base of its own (or when
    the archive does not hold the base at all, which gets a placeholder
    header); otherwise it becomes a header in its own right. So a
    grandchild sits at the left margin even when its own parent is
    printed as a header — one indent level, and the relationship the
    margin row loses is the one the record still states.

    That keeps the promise of one indent level without either
    duplicating a model that is both a base and a derivative, or — the
    worse failure — dropping a grandchild whose parent got demoted to a
    child. Both were live bugs (review, 2026-08-11), and the second is
    why this is not simply "skip claimed models".

    Args:
        summaries: Inventory rows, in any order.

    Returns:
        Rows in print order: each header followed by its derivatives,
        sorted by id so the output is stable between runs.
    """
    by_id = {summary.model_id: summary for summary in summaries}
    declared: dict[str, str] = {
        summary.model_id: summary.base_model
        for summary in summaries
        if summary.base_model and summary.base_model != summary.model_id
    }

    # A base that is itself a derivative cannot adopt, so its own
    # derivatives head their own groups. Read that off ``declared``
    # rather than off what the loop below has promoted so far: the
    # earlier version asked whether the base was already in ``headers``,
    # which made the answer depend on the id sort order instead of on
    # the records — see
    # ``test_grouping_does_not_depend_on_how_the_ids_sort`` for the live
    # case that exposed it.
    adopters = {model_id for model_id in by_id if model_id not in declared}
    headers: set[str] = set()
    children: dict[str, list[str]] = {}
    for model_id in sorted(by_id):
        base = declared.get(model_id)
        if base is not None and (base not in by_id or base in adopters):
            children.setdefault(base, []).append(model_id)
        else:
            headers.add(model_id)

    # A declared base the archive does not hold still heads a group: the
    # shelf saying "you have the conversion, not the model" is the
    # honest answer, and the reason ADR 0003 is legible at all.
    absent = {base for base in children if base not in by_id}

    rows: list[LineageRow] = []
    for header_id in sorted(headers | absent):
        found = by_id.get(header_id)
        if found is None and header_id not in absent:
            continue
        rows.append(LineageRow(found, header_id, depth=0, archived=found is not None))
        for child_id in sorted(children.get(header_id, [])):
            rows.append(LineageRow(by_id[child_id], child_id, depth=1))
    return rows
