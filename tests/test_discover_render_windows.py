"""Tests for the windowed half of llm_preserver.discover_render — spec 0015.

Split from test_discover_render.py (300-line rule): that file pins the
row / ladder / section / sanitization contract the renderers keep from
spec 0006, this one pins what windowing adds — the footer line, the key
hints, the absolute numbers a mid-sequence slice must print, the
"(continued)" section label, and the fixed-chrome line count the window
is sized against. Pure, zero I/O. These tests ARE the API contract:

    def key_hints(*, more_available: bool, back_available: bool) -> str
    def render_footer(footer: WindowFooter) -> str
    def tree_chrome_lines(
        current: ModelSummary, parents: Sequence[ParentLink], trail: Sequence[str]
    ) -> int

Verbatim strings (docs/cli.md documents these):

- ``render_footer`` builds ``showing {first}-{last} of {highest}``, then
  appends ``" — more (m)"`` when more rows can be fetched, then
  ``" · back (b)"`` when an earlier window can be re-shown:
  ``showing 21-39 of 80 — more (m) · back (b)``.
- ``key_hints`` is the comma-joined list of the keys on offer, always
  ending in quit: ``m = more, b = back a page, q = quit`` down to
  ``q = quit``. ``b`` spells out "a page" because the tree stage
  already means something by going back — the ``your path:`` trail
  (review round, 2026-08-06).
- A ``footer=None`` renders NO footer line at all — the empty listing
  (a tree with zero children) has no range to show.
- The first section label of a frame reads ``{relation} versions
  (continued):`` when ``section_continued`` says the window opened
  mid-section, and ``{relation} versions:`` otherwise. Later sections in
  the same frame are never marked continued.

Invariants:

- Rows print ``row.number``, not their index in the slice. A window
  holding rows 21..39 prints ``21.`` through ``39.`` — the heart of the
  spec, since a number that has been displayed must keep naming the
  same repo for the whole life of the stage.
- Every frame labels the section its first row belongs to, even
  mid-section: there is no scrollback to read the label from.
- ``tree_chrome_lines`` counts every line a tree frame spends on
  something other than child rows and their section headers, so the
  window sized from it cannot overrun the screen. It is checked against
  what ``render_tree_page`` actually emits, never against a constant.
"""

from llm_preserver.discover import ParentLink
from llm_preserver.discover_paging import NumberedRow, WindowFooter
from llm_preserver.discover_render import (
    key_hints,
    render_footer,
    render_search_page,
    render_tree_page,
    tree_chrome_lines,
)
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


def window(first, last, *, more=False, back=False, highest=None):
    """Build a WindowFooter for a visible range."""
    return WindowFooter(
        first=first,
        last=last,
        highest=last if highest is None else highest,
        more_available=more,
        back_available=back,
    )


def rows_from(number, *relations):
    """Number one row per relation given, starting at ``number``."""
    return [
        NumberedRow(
            number=number + offset,
            summary=summary(f"acme/child-{number + offset}", relation=relation),
        )
        for offset, relation in enumerate(relations)
    ]


def parent_link(repo_id, status="ok", **overrides):
    """Build one ancestry link (a not-found link carries no summary)."""
    link_summary = None if status == "not-found" else summary(repo_id, **overrides)
    return ParentLink(requested_id=repo_id, summary=link_summary, status=status)


def has_number(line, n):
    """True when the line starts (after leading whitespace) with 'n.'."""
    return line.lstrip().startswith(f"{n}.")


def tree(children, *, current=None, parents=(), footer=None, trail=(), section_continued=False):
    """Render one tree frame with the pinned ladder and a child slice."""
    return render_tree_page(
        current if current is not None else summary("acme/tiny-chat"),
        list(parents),
        children,
        footer=footer,
        trail=trail,
        section_continued=section_continued,
    )


# --- key_hints ---------------------------------------------------------


def test_key_hints_offer_more_then_back_then_quit_when_both_exist():
    assert (
        key_hints(more_available=True, back_available=True) == "m = more, b = back a page, q = quit"
    )


def test_key_hints_drop_back_on_the_first_window():
    assert key_hints(more_available=True, back_available=False) == "m = more, q = quit"


def test_key_hints_drop_more_once_the_hub_has_no_further_rows():
    assert key_hints(more_available=False, back_available=True) == "b = back a page, q = quit"


def test_key_hints_are_quit_alone_when_nothing_else_is_offered():
    assert key_hints(more_available=False, back_available=False) == "q = quit"


# --- render_footer -----------------------------------------------------


def test_footer_names_the_visible_range_and_the_highest_number_assigned():
    assert render_footer(window(1, 20, more=True)) == "showing 1-20 of 20 — more (m)"


def test_footer_offers_back_after_more_when_both_keys_are_live():
    footer = window(21, 39, highest=80, more=True, back=True)
    assert render_footer(footer) == "showing 21-39 of 80 — more (m) · back (b)"


def test_footer_offers_back_alone_on_the_last_window():
    assert render_footer(window(61, 80, back=True)) == "showing 61-80 of 80 · back (b)"


def test_footer_is_the_range_alone_when_one_window_holds_everything():
    assert render_footer(window(1, 3)) == "showing 1-3 of 3"


# --- the footer in a frame ---------------------------------------------


def test_the_search_frame_ends_with_its_rendered_footer():
    footer = window(21, 22, highest=40, more=True, back=True)
    lines = render_search_page(rows_from(21, None, None), query="tiny chat", footer=footer)
    assert lines[-1] == render_footer(footer)


def test_the_tree_frame_ends_with_its_rendered_footer():
    footer = window(3, 4, highest=44, more=True, back=True)
    lines = tree(rows_from(3, "quantized", "quantized"), footer=footer)
    assert lines[-1] == render_footer(footer)


def test_a_search_frame_without_a_footer_renders_no_showing_line():
    lines = render_search_page(rows_from(1, None, None), query="tiny chat", footer=None)
    assert all("showing" not in line for line in lines)
    assert any("acme/child-1" in line for line in lines)


def test_an_empty_tree_listing_renders_no_footer_line():
    # A tree with zero children has no range to report, but the frame
    # must still offer the stable pull key.
    lines = tree([], footer=None)
    assert all("showing" not in line for line in lines)
    assert lines[-1] == "  0. pull this repo (acme/tiny-chat)"


# --- absolute numbers ---------------------------------------------------


def test_a_search_window_starting_mid_sequence_prints_its_absolute_numbers():
    rows = rows_from(21, *[None] * 19)
    lines = render_search_page(
        rows, query="tiny chat", footer=window(21, 39, highest=80, back=True)
    )
    assert has_number(next(line for line in lines if "acme/child-21" in line), 21)
    assert has_number(next(line for line in lines if "acme/child-39" in line), 39)
    assert not any(has_number(line, 1) for line in lines)


def test_a_tree_window_prints_child_numbers_assigned_at_fetch_time():
    # Not "3." and "4." because they follow two ancestry rows in this
    # frame: they are rows 21 and 22 of the sequence and say so.
    parents = [parent_link("acme/base", base_model="acme/root"), parent_link("acme/root")]
    lines = tree(rows_from(21, "quantized", "quantized"), parents=parents)
    assert has_number(next(line for line in lines if "acme/child-21" in line), 21)
    assert has_number(next(line for line in lines if "acme/child-22" in line), 22)
    assert not any(has_number(line, 3) for line in lines)


def test_the_ancestry_ladder_keeps_its_own_numbers_in_a_late_window():
    parents = [parent_link("acme/base", base_model="acme/root"), parent_link("acme/root")]
    lines = tree(rows_from(21, "quantized"), parents=parents)
    assert has_number(next(line for line in lines if "acme/root" in line), 1)
    assert has_number(next(line for line in lines if "acme/base" in line), 2)


def test_the_pull_key_stays_zero_in_a_late_window():
    lines = tree(rows_from(61, "quantized"), footer=window(61, 61, highest=80, back=True))
    assert "  0. pull this repo (acme/tiny-chat)" in lines


# --- section labels -----------------------------------------------------


def test_a_frame_labels_the_section_its_first_row_belongs_to():
    # Mid-section or not, a frame that cannot be scrolled back from must
    # say what it is listing.
    lines = tree(rows_from(21, "quantized", "quantized"))
    label = lines.index("quantized versions:")
    assert label < next(index for index, line in enumerate(lines) if "acme/child-21" in line)


def test_a_window_opening_mid_section_marks_its_first_label_continued():
    lines = tree(rows_from(21, "quantized", "quantized"), section_continued=True)
    assert "quantized versions (continued):" in lines
    assert "quantized versions:" not in lines


def test_only_the_first_label_of_a_continued_frame_is_marked_continued():
    lines = tree(rows_from(21, "quantized", "finetune"), section_continued=True)
    assert "quantized versions (continued):" in lines
    assert "finetune versions:" in lines


def test_a_window_that_opens_a_section_labels_it_plainly():
    lines = tree(rows_from(21, "quantized", "quantized"), section_continued=False)
    assert "quantized versions:" in lines
    assert all("(continued)" not in line for line in lines)


# --- tree_chrome_lines --------------------------------------------------


def test_the_chrome_count_covers_every_line_of_a_childless_frame():
    # The window is sized as "terminal height less chrome": undercount
    # and the frame overruns the screen the spec exists to fit.
    current = summary("acme/tiny-chat")
    parents = [parent_link("acme/base", base_model="acme/root"), parent_link("acme/root")]
    trail = ["acme/base", "acme/tiny-chat"]
    frame = tree([], current=current, parents=parents, footer=window(1, 1, more=True), trail=trail)
    assert tree_chrome_lines(current, parents, trail) >= len(frame)


def test_the_chrome_count_tracks_the_ladder_line_for_line():
    current = summary("acme/tiny-chat")
    parents = [parent_link("acme/base", base_model="acme/root"), parent_link("acme/root")]
    trail = ["acme/base", "acme/tiny-chat"]
    rendered_delta = len(tree([], current=current, parents=parents, trail=trail)) - len(
        tree([], current=current)
    )
    counted_delta = tree_chrome_lines(current, parents, trail) - tree_chrome_lines(current, (), ())
    assert counted_delta == rendered_delta


def test_the_chrome_count_charges_one_line_per_extra_ancestry_hop():
    current = summary("acme/tiny-chat")
    one = [parent_link("acme/base")]
    three = [parent_link("acme/base"), parent_link("acme/mid"), parent_link("acme/root")]
    assert tree_chrome_lines(current, three, ()) - tree_chrome_lines(current, one, ()) == 2


def test_the_chrome_count_charges_a_not_found_parent_that_takes_no_number():
    current = summary("acme/tiny-chat")
    one = [parent_link("acme/base")]
    with_dead = [parent_link("acme/base"), parent_link("dead/root", status="not-found")]
    assert tree_chrome_lines(current, with_dead, ()) - tree_chrome_lines(current, one, ()) == 1


def test_the_chrome_count_charges_the_breadcrumb_line():
    current = summary("acme/tiny-chat")
    hopped = ["acme/base", "acme/tiny-chat"]
    assert tree_chrome_lines(current, (), hopped) - tree_chrome_lines(current, (), ()) == 1


def test_a_single_hop_trail_costs_no_breadcrumb_line():
    # One entry means you never hopped; the breadcrumb is not printed.
    current = summary("acme/tiny-chat")
    assert tree_chrome_lines(current, (), ["acme/tiny-chat"]) == tree_chrome_lines(current, (), ())
