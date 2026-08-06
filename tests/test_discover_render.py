"""Tests for llm_preserver.discover_render — specs 0006 and 0015.

Pins the pure listing renderers: data in, ``list[str]`` out, no I/O.
The window-dependent half — footer wording, key hints, absolute numbers
in a mid-sequence slice, continued section labels, and the fixed-chrome
line count — lives in ``test_discover_render_windows.py`` (300-line
rule). These tests ARE the API contract:

    def render_search_page(
        rows: Sequence[NumberedRow], *, query: str, footer: WindowFooter | None
    ) -> list[str]

    def render_tree_page(
        current: ModelSummary,
        parents: Sequence[ParentLink],    # nearest parent first (build_parent_chain order)
        children: Sequence[NumberedRow],  # one window's slice, hub order, numbers pre-assigned
        *,
        footer: WindowFooter | None,
        trail: Sequence[str] = (),
        section_continued: bool = False,
    ) -> list[str]

Rendering contract (substrings and invariants, not exact layout):

- A numbered row's line starts (after any leading whitespace) with
  ``"N."`` where N is ``row.number`` — the number assigned when the row
  was appended (spec 0015), never its position within the slice. Search
  rows render in the order given (the hub's order, never re-sorted).
- Tree numbering is continuous across sections: the renderer numbers the
  navigable parents (status "ok"/"renamed") 1..P itself — the ancestry
  ladder is pinned, fetched once, never paged — then the children print
  the numbers they already carry, then the stable "0" pull line for the
  current repo. A "not-found" parent renders its dead id with a "not
  found on the hub" note and takes NO number; a "renamed" parent renders
  one line carrying the requested id, the current id, and "renamed".
- Each row shows the repo id, the downloads count (raw digits appear
  somewhere in the line), and the last-modified date (the ISO date part
  appears); "gated" appears in a row's line only when that row is gated;
  a None fact never renders as the literal "None".
- Children are grouped under a header line containing the relation word
  ("quantized", "finetune", ...), before its group's rows.
- Every hub-supplied string is sanitized via ``render.clean_text`` — no
  raw ESC byte (``\\x1b``) in any output line, and the sanitized row
  still renders (hostile rows are cleaned, never dropped).
- Pure and deterministic: equal inputs yield identical lists.
"""

from llm_preserver.discover import ParentLink
from llm_preserver.discover_paging import NumberedRow, WindowFooter
from llm_preserver.discover_render import render_search_page, render_tree_page
from llm_preserver.hub_discovery import ModelSummary


def summary(repo_id, **overrides):
    """Build a ModelSummary with all-None facts unless overridden."""
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def numbered(summaries, start=1):
    """Number summaries from ``start``, the way RowSequence assigns them."""
    return [
        NumberedRow(number=start + offset, summary=item) for offset, item in enumerate(summaries)
    ]


def line_with(lines, *fragments):
    """Return the single line containing every fragment; fail otherwise."""
    matches = [line for line in lines if all(fragment in line for fragment in fragments)]
    assert len(matches) == 1, f"expected exactly one line with {fragments!r}, got {matches!r}"
    return matches[0]


def line_index(lines, fragment):
    """Return the index of the single line containing ``fragment``."""
    return lines.index(line_with(lines, fragment))


def has_number(line, n):
    """True when the line starts (after leading whitespace) with 'n.'."""
    return line.lstrip().startswith(f"{n}.")


def search_rows():
    return [
        summary("alpha/one", downloads=41, last_modified="2026-07-01T12:00:00+00:00"),
        summary(
            "beta/two",
            downloads=87,
            last_modified="2026-06-15T00:00:00+00:00",
            gated="manual",
        ),
        summary("gamma/three"),
    ]


def render_search(rows=None, *, query="tiny chat", footer=None, start=1):
    actual_rows = search_rows() if rows is None else rows
    return render_search_page(numbered(actual_rows, start), query=query, footer=footer)


def tree_children():
    return [
        summary(
            "q/tiny-q4",
            downloads=41,
            last_modified="2026-07-01T12:00:00+00:00",
            relation="quantized",
        ),
        summary("q/tiny-q8", downloads=7, gated="manual", relation="quantized"),
        summary("f/tiny-ft", downloads=87, relation="finetune"),
    ]


def tree_parents():
    return [
        ParentLink(
            requested_id="acme/base",
            summary=summary("acme/base", base_model="acme/root"),
            status="ok",
        ),
        ParentLink(requested_id="acme/root", summary=summary("acme/root"), status="ok"),
    ]


def navigable_count(parents):
    """How many parent links the renderer numbers (not-found takes no number)."""
    return sum(1 for link in parents if link.summary is not None and link.status != "not-found")


def render_tree(current=None, parents=(), children=(), *, footer=None, trail=(), start=None):
    """Render a tree frame, numbering children after the pinned ladder."""
    links = list(parents)
    child_start = navigable_count(links) + 1 if start is None else start
    return render_tree_page(
        current if current is not None else summary("acme/tiny-chat"),
        links,
        numbered(list(children), child_start),
        footer=footer,
        trail=trail,
    )


# --- render_search_page ------------------------------------------------


def test_search_rows_print_the_numbers_they_carry():
    lines = render_search()
    assert has_number(line_with(lines, "alpha/one"), 1)
    assert has_number(line_with(lines, "beta/two"), 2)
    assert has_number(line_with(lines, "gamma/three"), 3)


def test_search_row_shows_downloads_count():
    lines = render_search()
    assert "41" in line_with(lines, "alpha/one")
    assert "87" in line_with(lines, "beta/two")


def test_search_row_shows_last_modified_date():
    assert "2026-07-01" in line_with(render_search(), "alpha/one")


def test_missing_facts_never_render_as_none_literal():
    assert "None" not in line_with(render_search(), "gamma/three")


def test_gated_marker_appears_only_on_gated_rows():
    lines = render_search()
    assert "gated" in line_with(lines, "beta/two")
    assert "gated" not in line_with(lines, "alpha/one")
    assert "gated" not in line_with(lines, "gamma/three")


def test_search_header_names_the_query_and_the_hubs_order():
    assert render_search()[0] == "hub search results for 'tiny chat' (the hub's relevance order):"


def test_search_sanitizes_hub_escape_bytes_without_dropping_the_row():
    lines = render_search([summary("evil/\x1b[31mred", downloads=1)])
    assert all("\x1b" not in line for line in lines)
    assert any("evil/" in line for line in lines)


def test_search_rendering_is_deterministic():
    # Separately built but equal inputs must yield byte-identical output.
    assert render_search() == render_search()


# --- render_tree_page --------------------------------------------------


def test_tree_header_names_the_repo_whose_tree_is_shown():
    # Every frame is self-sufficient: windowing removes the scrollback
    # a user could otherwise consult for "which repo am I in" (0015).
    assert render_tree(children=tree_children())[0] == "model tree for acme/tiny-chat:"


def test_ok_parent_link_shows_the_parent_repo_id():
    lines = render_tree(parents=tree_parents())
    assert line_with(lines, "acme/base")
    assert line_with(lines, "acme/root")


def test_renamed_parent_shows_both_ids_with_a_marker():
    renamed = ParentLink(requested_id="old/base", summary=summary("new/base"), status="renamed")
    line = line_with(render_tree(parents=[renamed]), "old/base", "new/base")
    assert "renamed" in line


def test_not_found_parent_shows_dead_id_with_note_and_no_number():
    dead = ParentLink(requested_id="dead/base", summary=None, status="not-found")
    line = line_with(render_tree(parents=[dead]), "dead/base")
    assert "not found on the hub" in line
    assert not line.lstrip()[0].isdigit()


def test_children_grouped_under_relation_header_lines():
    lines = render_tree(children=tree_children())
    quantized_header = line_index(lines, "quantized")
    finetune_header = line_index(lines, "finetune")
    assert quantized_header < line_index(lines, "q/tiny-q4") < finetune_header
    assert quantized_header < line_index(lines, "q/tiny-q8") < finetune_header
    assert finetune_header < line_index(lines, "f/tiny-ft")


def test_tree_numbering_is_continuous_across_sections():
    # Ancestry renders as a ladder, ROOT FIRST (live-use adjudication
    # 2026-07-13), then children, then the pull option.
    lines = render_tree(parents=tree_parents(), children=tree_children())
    assert has_number(line_with(lines, "acme/root"), 1)
    assert has_number(line_with(lines, "acme/base"), 2)
    assert has_number(line_with(lines, "q/tiny-q4"), 3)
    assert has_number(line_with(lines, "q/tiny-q8"), 4)
    assert has_number(line_with(lines, "f/tiny-ft"), 5)
    # "0" is the stable pull key (adjudicated 2026-07-13).
    assert has_number(line_with(lines, "pull this repo"), 0)


def test_ancestry_ladder_marks_root_and_current_repo():
    lines = render_tree(parents=tree_parents(), children=tree_children())
    assert "[original — no parent]" in line_with(lines, "acme/root")
    assert "[this repo — you are here]" in line_with(lines, "acme/tiny-chat", "you are here")
    root_line = line_with(lines, "acme/root")
    nearest_line = line_with(lines, "acme/base")
    assert lines.index(root_line) < lines.index(nearest_line)  # root at top


def test_not_found_parent_takes_no_number_slot():
    parents = [
        ParentLink(requested_id="acme/base", summary=summary("acme/base"), status="ok"),
        ParentLink(requested_id="dead/root", summary=None, status="not-found"),
    ]
    lines = render_tree(parents=parents, children=[summary("q/tiny-q4", relation="quantized")])
    assert has_number(line_with(lines, "acme/base"), 1)
    assert has_number(line_with(lines, "q/tiny-q4"), 2)
    assert has_number(line_with(lines, "pull this repo"), 0)


def test_pull_current_option_is_the_only_number_on_a_bare_tree():
    lines = render_tree()
    numbered_lines = [line for line in lines if line.lstrip().split(".")[0].isdigit()]
    assert numbered_lines == ["  0. pull this repo (acme/tiny-chat)"]


def test_child_row_shows_downloads_and_date():
    line = line_with(render_tree(children=tree_children()), "q/tiny-q4")
    assert "41" in line
    assert "2026-07-01" in line


def test_gated_child_row_is_marked():
    lines = render_tree(children=tree_children())
    assert "gated" in line_with(lines, "q/tiny-q8")
    assert "gated" not in line_with(lines, "q/tiny-q4")


def test_tree_sanitizes_hub_escape_bytes_without_dropping_the_row():
    lines = render_tree(children=[summary("evil/\x1b[31mred", relation="quantized")])
    assert all("\x1b" not in line for line in lines)
    assert any("evil/" in line for line in lines)


def test_tree_rendering_is_deterministic():
    # Separately built but equal inputs must yield byte-identical output.
    footer = WindowFooter(first=3, last=5, highest=9, more_available=True, back_available=True)
    first = render_tree(parents=tree_parents(), children=tree_children(), footer=footer)
    second = render_tree(parents=tree_parents(), children=tree_children(), footer=footer)
    assert first == second
