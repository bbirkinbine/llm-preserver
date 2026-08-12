"""Lineage rendering — spec 0017 pass 5, criteria 7 and 8.

ADR 0003 took the structural statement away: a nested path used to say
"this quant derives from that model" without any tool reading it. These
tests pin where that statement comes back — grouped in ``status``, both
directions in ``show``, and in prose in ``MODEL-RECORD.md`` for the
reader with ``ls``, ``cat``, and no ``llm-preserver`` at all.

The absent-base case is the one worth being careful about: three
directories on the live archive are conversions of models it does not
hold, and a shelf that quietly omitted that row would be hiding exactly
the fact the flat layout exists to expose.
"""

import click
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.lineage import group_by_lineage
from llm_preserver.render import render_model_record

runner = CliRunner()

BASE_ID = "acme/tiny-chat"
QUANT_ID = "unsloth/tiny-chat-GGUF"
ABSENT_BASE = "meta-models/never-archived"


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    assert runner.invoke(app, ["init", str(archive)]).exit_code == 0
    return archive


def seed(archive, write_model, sample_record_dict, model_id, *, base=None):
    """Write a self-consistent record for ``model_id``."""
    owner, name = model_id.split("/")
    record = sample_record_dict(name=name, hub_id=model_id)
    if base is not None:
        record["base_model"] = base
        record["base_model_source"] = "card"
    return write_model(archive, record, creator=owner, model=name)


def status_of(archive):
    result = runner.invoke(app, ["status", str(archive)])
    assert result.exit_code == 0, click.unstyle(result.output)
    return click.unstyle(result.output)


def test_a_derivative_is_indented_under_the_base_it_declares(
    tmp_path, write_model, sample_record_dict
):
    archive = init_archive_dir(tmp_path)
    seed(archive, write_model, sample_record_dict, BASE_ID)
    seed(archive, write_model, sample_record_dict, QUANT_ID, base=BASE_ID)

    lines = status_of(archive).splitlines()
    base_line = next(i for i, line in enumerate(lines) if line.startswith(BASE_ID))
    quant_line = next(i for i, line in enumerate(lines) if QUANT_ID in line)

    assert quant_line == base_line + 1
    assert lines[quant_line].startswith("  ")  # indented under its base


def test_a_base_the_archive_does_not_hold_still_gets_a_row(
    tmp_path, write_model, sample_record_dict
):
    # The live archive's sharpest case: three directories are
    # conversions of models it does not hold, and the shelf has to say
    # so rather than list the quant as though nothing were missing.
    archive = init_archive_dir(tmp_path)
    seed(archive, write_model, sample_record_dict, QUANT_ID, base=ABSENT_BASE)

    out = status_of(archive)

    assert f"({ABSENT_BASE})" in out
    assert "not archived" in out


def test_an_unrelated_model_is_not_indented(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    seed(archive, write_model, sample_record_dict, BASE_ID)

    line = next(line for line in status_of(archive).splitlines() if line.startswith(BASE_ID))

    assert not line.startswith(" ")


def test_show_names_the_repos_that_derive_from_this_one(tmp_path, write_model, sample_record_dict):
    # The direction only the archive as a whole knows: the base's own
    # record says nothing about what was built from it.
    archive = init_archive_dir(tmp_path)
    seed(archive, write_model, sample_record_dict, BASE_ID)
    seed(archive, write_model, sample_record_dict, QUANT_ID, base=BASE_ID)

    result = runner.invoke(app, ["show", BASE_ID, str(archive)])

    assert result.exit_code == 0
    assert QUANT_ID in click.unstyle(result.output)


def test_show_names_the_base_this_repo_declares(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    seed(archive, write_model, sample_record_dict, QUANT_ID, base=BASE_ID)

    result = runner.invoke(app, ["show", QUANT_ID, str(archive)])

    assert result.exit_code == 0
    assert f"Derived from: {BASE_ID}" in click.unstyle(result.output)


def test_a_model_with_no_lineage_claims_none(tmp_path, write_model, sample_record_dict):
    archive = init_archive_dir(tmp_path)
    seed(archive, write_model, sample_record_dict, BASE_ID)

    out = click.unstyle(runner.invoke(app, ["show", BASE_ID, str(archive)]).output)

    assert "Derived from" not in out


def test_the_markdown_states_the_relationship_in_prose(sample_record_dict):
    # ADR 0001's durability test: a reader with cat and no tool must
    # still learn that this directory derives from another.
    from llm_preserver.records import ModelRecord

    record = ModelRecord.model_validate(
        sample_record_dict(hub_id=QUANT_ID, base_model=BASE_ID, base_model_source="migrated")
    )

    rendered = render_model_record(record, file_header=False)

    assert f"Derived from: {BASE_ID}" in rendered
    assert "used to sit under" in rendered  # the attribution, in words


def test_a_chain_lists_every_model_exactly_once(sample_record_dict):
    """base <- instruct <- quant is an ordinary hub shape, and both ways
    of getting it wrong were live bugs (review, 2026-08-11): listing the
    middle model twice (double-counting its size in the one column ADR
    0003 relies on), then dropping the leaf entirely when the duplicate
    was removed. Assert the whole row list — a set of depths is
    satisfied by both failures."""
    from llm_preserver.archive import ModelSummary

    rows = group_by_lineage(
        [
            ModelSummary(model_id="a/base"),
            ModelSummary(model_id="b/mid", base_model="a/base"),
            ModelSummary(model_id="c/leaf", base_model="b/mid"),
        ]
    )

    # b/mid is a child, so it cannot adopt: c/leaf heads its own group
    # rather than being indented under a model it does not derive from.
    assert [(row.model_id, row.depth) for row in rows] == [
        ("a/base", 0),
        ("b/mid", 1),
        ("c/leaf", 0),
    ]


def test_a_grandchild_heads_its_own_group_even_when_its_parent_is_a_header(
    sample_record_dict,
):
    """The one shape the 2026-08-12 fix renders differently, pinned so it
    is a decision rather than an accident (adversarial review).

    ``b`` declares ``a``, which declares a base the archive does not
    hold — so ``a`` is indented under a placeholder and ``b``, unable to
    be adopted by a child, is printed at the left margin. ``c`` declares
    ``b``. The old sorted-pass rule indented ``c`` under ``b``; adopting
    only on "declares no base of its own" does not, because ``b`` is
    itself a derivative. One indent level is what makes this a trade
    rather than a loss: nesting ``c`` under ``b`` would put a derivative
    of a derivative on screen without its own parent's lineage visible.
    """
    from llm_preserver.archive import ModelSummary

    rows = group_by_lineage(
        [
            ModelSummary(model_id="a/mid", base_model="gone/missing"),
            ModelSummary(model_id="b/leaf", base_model="a/mid"),
            ModelSummary(model_id="c/tip", base_model="b/leaf"),
        ]
    )

    # Headers print in id order, each followed by its own derivatives —
    # so the placeholder group lands last and carries `a/mid` with it.
    assert [(row.model_id, row.depth, row.archived) for row in rows] == [
        ("b/leaf", 0, True),
        ("c/tip", 0, True),
        ("gone/missing", 0, False),
        ("a/mid", 1, True),
    ]


def test_no_model_is_ever_listed_twice(sample_record_dict):
    from llm_preserver.archive import ModelSummary

    summaries = [
        ModelSummary(model_id="a/base"),
        ModelSummary(model_id="b/mid", base_model="a/base"),
        ModelSummary(model_id="c/leaf", base_model="b/mid"),
        ModelSummary(model_id="d/other", base_model="a/base"),
        ModelSummary(model_id="e/orphan", base_model="gone/missing"),
    ]

    rows = group_by_lineage(summaries)

    listed = [row.model_id for row in rows if row.archived]
    assert sorted(listed) == sorted(s.model_id for s in summaries)
    assert len(listed) == len(set(listed))


def test_grouping_does_not_depend_on_how_the_ids_sort(sample_record_dict):
    """Live-use bug (2026-08-12): the archive held both
    ``unsloth/GLM-4.7-Flash-GGUF`` and the ``zai-org/GLM-4.7-Flash`` it
    declares, and ``status`` printed them as two unrelated top-level
    rows while all eight other pairs grouped. The single pass over
    sorted ids adopted only bases already promoted to headers, so a base
    whose id sorts *after* its derivative had not been seen yet.

    Same lineage, two id orderings, one shape — the assertion the
    ordering-dependent version cannot satisfy.
    """
    from llm_preserver.archive import ModelSummary

    def shape(base_id: str, quant_id: str) -> list[tuple[str, int]]:
        rows = group_by_lineage(
            [
                ModelSummary(model_id=quant_id, base_model=base_id),
                ModelSummary(model_id=base_id),
            ]
        )
        return [(row.model_id, row.depth) for row in rows]

    assert shape("acme/x", "unsloth/x-GGUF") == [("acme/x", 0), ("unsloth/x-GGUF", 1)]
    assert shape("zai-org/x", "unsloth/x-GGUF") == [("zai-org/x", 0), ("unsloth/x-GGUF", 1)]


def test_status_indents_a_quant_whose_base_sorts_after_it(
    tmp_path, write_model, sample_record_dict
):
    # The end-to-end half of the same bug: what the shelf prints is what
    # the human reads to decide whether a full-precision pull closed a
    # gap, so the unit-level shape is not enough on its own.
    archive = init_archive_dir(tmp_path)
    late_base = "zzz-org/tiny-chat"
    seed(archive, write_model, sample_record_dict, late_base)
    seed(archive, write_model, sample_record_dict, QUANT_ID, base=late_base)

    lines = status_of(archive).splitlines()
    base_line = next(i for i, line in enumerate(lines) if line.startswith(late_base))
    quant_line = next(i for i, line in enumerate(lines) if QUANT_ID in line)

    assert quant_line == base_line + 1
    assert lines[quant_line].startswith("  ")


def test_a_mutual_cycle_terminates_and_lists_both(sample_record_dict):
    # Two records each declaring the other. Nothing forbids it in a
    # record, so the renderer must not loop or duplicate.
    from llm_preserver.archive import ModelSummary

    rows = group_by_lineage(
        [
            ModelSummary(model_id="a/x", base_model="b/y"),
            ModelSummary(model_id="b/y", base_model="a/x"),
        ]
    )

    assert sorted(row.model_id for row in rows) == ["a/x", "b/y"]


def test_a_record_declaring_itself_as_its_base_does_not_loop(sample_record_dict):
    from llm_preserver.archive import ModelSummary

    rows = group_by_lineage([ModelSummary(model_id="a/self", base_model="a/self")])

    assert [(row.model_id, row.depth) for row in rows] == [("a/self", 0)]
