"""Tests for the "b" (back) pick in llm_preserver.discover — spec 0015.

Split from test_discover_nav.py (300-line rule); the nav contract this
extends is stated in that file's docstring. Windowed paging shows one
screen at a time and removes what scrollback used to provide, so a
frame needs a way back through rows already fetched. These tests ARE
the contract for it:

    Option.kind gains "back"; the pick is Option("b", "back", None).

    DiscoveryPage gains ``back_available: bool = False`` — the default
    keeps every pre-0015 construction (stage/options/more_available)
    valid and back-less.

``parse_pick`` gates "b" exactly the way it gates "m": offered only
when the page says an earlier window exists, invalid otherwise, with
the same strip/lowercase handling. "b" and "m" are independently
gated — the end of the hub's rows does not take the way back — and "q"
and the numbered picks are unaffected by either flag.
"""

from typing import get_args, get_type_hints

from llm_preserver.discover import DiscoveryPage, Option, parse_pick
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


def make_page(n_rows=3, stage="tree", more_available=False, back_available=False):
    """Build a DiscoveryPage of ``n_rows`` navigate options, keys "1"..str(n)."""
    options = tuple(
        Option(key=str(i + 1), kind="navigate", summary=summary(f"acme/m{i}"))
        for i in range(n_rows)
    )
    return DiscoveryPage(
        stage=stage,
        options=options,
        more_available=more_available,
        back_available=back_available,
    )


BACK = Option(key="b", kind="back", summary=None)


# --- shapes ------------------------------------------------------------


def test_option_kinds_include_back_alongside_the_0006_picks():
    kinds = set(get_args(get_type_hints(Option)["kind"]))
    assert kinds == {"navigate", "select", "more", "back", "quit"}


def test_back_available_defaults_to_false_on_a_page_built_without_it():
    # Pre-0015 call sites pass stage/options/more_available only.
    page = DiscoveryPage(stage="search", options=(), more_available=False)
    assert page.back_available is False


# --- parse_pick: "b" ---------------------------------------------------


def test_back_returns_the_back_pick_when_an_earlier_window_exists():
    assert parse_pick("b", make_page(3, back_available=True)) == BACK


def test_back_is_invalid_on_the_first_window():
    assert parse_pick("b", make_page(3, back_available=False)) is None


def test_back_pick_is_case_insensitive():
    assert parse_pick("B", make_page(3, back_available=True)) == BACK


def test_back_input_whitespace_is_stripped_before_matching():
    assert parse_pick(" b ", make_page(3, back_available=True)) == BACK


def test_back_is_offered_while_the_hub_rows_are_exhausted():
    # Reaching the end of the hub's rows must not take the way back.
    page = make_page(3, more_available=False, back_available=True)
    assert parse_pick("b", page) == BACK
    assert parse_pick("m", page) is None


def test_more_is_offered_on_the_first_window_where_back_is_not():
    page = make_page(3, more_available=True, back_available=False)
    assert parse_pick("m", page) == Option(key="m", kind="more", summary=None)
    assert parse_pick("b", page) is None


# --- parse_pick: "b" leaves the other picks alone ----------------------


def test_numbered_picks_still_resolve_on_a_page_offering_back():
    page = make_page(3, back_available=True)
    assert parse_pick("2", page) == page.options[1]


def test_quit_is_valid_whether_or_not_back_is_offered():
    quit_pick = Option(key="q", kind="quit", summary=None)
    assert parse_pick("q", make_page(3, back_available=True)) == quit_pick
    assert parse_pick("q", make_page(3, back_available=False)) == quit_pick


def test_garbage_starting_with_b_is_still_invalid():
    page = make_page(3, more_available=True, back_available=True)
    assert parse_pick("b1", page) is None
    assert parse_pick("back", page) is None
