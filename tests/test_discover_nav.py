"""Tests for llm_preserver.discover — spec 0006, Phase B (navigation).

Pins the pure discovery state machine: zero I/O, dataclass equality,
deterministic. These tests ARE the API contract:

    Stage = Literal["search", "tree"]

    @dataclass(frozen=True)
    class Option:
        key: str                      # what the user types: "1".."N", "m", "q"
        kind: Literal["navigate", "select", "more", "quit"]
        summary: ModelSummary | None  # target repo for navigate/select; None otherwise

    @dataclass(frozen=True)
    class DiscoveryPage:
        stage: Stage
        options: tuple[Option, ...]   # numbered picks only, keys "1".."N" in display order
        more_available: bool          # True → "m" is a valid pick

    def parse_pick(raw: str, page: DiscoveryPage) -> Option | None

    @dataclass(frozen=True)
    class ParentLink:
        requested_id: str
        summary: ModelSummary | None  # None exactly when status == "not-found"
        status: Literal["ok", "not-found", "renamed"]

    def build_parent_chain(
        repo_id: str, base_model: str | None,
        fetch: Callable[[str], ModelSummary],
    ) -> list[ParentLink]

``parse_pick`` strips whitespace and lowercases letter picks. A digit
string whose integer value is in ``1..len(options)`` returns
``options[value - 1]``; "q" always returns ``Option("q", "quit",
None)``; "m" returns ``Option("m", "more", None)`` only when
``more_available`` is True; everything else (empty, zero, out of
range, non-digit garbage) returns None.

``build_parent_chain`` walks repeated ``base_model`` hops starting
from the picked repo's declared parent, nearest parent first. Per hop:
``fetch`` raising ``PullUserError`` (the hub 404 mapping) records a
"not-found" link with ``summary=None`` and stops; a summary whose
``repo_id`` equals the requested id is "ok"; a differing ``repo_id``
(hub rename redirect) is "renamed" and the walk continues from the
*returned* summary's ``base_model``. The walk stops on an undeclared
parent or when the next id was already seen (seen = the starting
``repo_id`` plus every requested and returned id — the cycle guard).
"""

from typing import get_args

import pytest

import llm_preserver.hub as hub
from llm_preserver.discover import (
    DiscoveryPage,
    Option,
    ParentLink,
    Stage,
    build_parent_chain,
    parse_pick,
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


def make_page(n_rows=3, stage="search", more_available=False, last_kind=None):
    """Build a DiscoveryPage of ``n_rows`` navigate options, keys "1"..str(n).

    ``last_kind`` overrides the kind of the final option (e.g. "select"
    for the tree stage's pull-this-repo pick).
    """
    options = []
    for i in range(n_rows):
        kind = last_kind if (last_kind and i == n_rows - 1) else "navigate"
        target = summary(f"acme/m{i}")
        options.append(Option(key=str(i + 1), kind=kind, summary=target))
    return DiscoveryPage(stage=stage, options=tuple(options), more_available=more_available)


def make_fetch(summaries, calls=None):
    """Fake parent-hop fetch: canned summaries, PullUserError on a miss."""

    def fetch(repo_id):
        if calls is not None:
            calls.append(repo_id)
        if repo_id not in summaries:
            raise hub.PullUserError(f"repo, revision, or file not found: {repo_id}")
        return summaries[repo_id]

    return fetch


# --- shapes ------------------------------------------------------------


def test_stage_names_the_two_discovery_stages():
    assert set(get_args(Stage)) == {"search", "tree"}


def test_option_is_frozen():
    option = Option(key="1", kind="navigate", summary=summary("acme/m0"))
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        option.key = "2"  # type: ignore[misc]


def test_discovery_page_is_frozen():
    page = make_page(1)
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        page.more_available = True  # type: ignore[misc]


def test_parent_link_is_frozen():
    link = ParentLink(requested_id="acme/base", summary=summary("acme/base"), status="ok")
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        link.status = "renamed"  # type: ignore[misc]


# --- parse_pick --------------------------------------------------------


def test_digit_pick_returns_that_numbered_option():
    page = make_page(3)
    assert parse_pick("2", page) == page.options[1]


def test_digit_pick_returns_select_option_with_its_target():
    page = make_page(3, stage="tree", last_kind="select")
    pick = parse_pick("3", page)
    assert pick == page.options[2]
    assert pick.kind == "select"
    assert pick.summary == summary("acme/m2")


def test_more_returns_more_pick_when_page_advertises_more():
    page = make_page(3, more_available=True)
    assert parse_pick("m", page) == Option(key="m", kind="more", summary=None)


def test_more_is_invalid_when_page_is_exhausted():
    page = make_page(3, more_available=False)
    assert parse_pick("m", page) is None


def test_quit_is_valid_even_on_an_exhausted_page():
    page = make_page(3, more_available=False)
    assert parse_pick("q", page) == Option(key="q", kind="quit", summary=None)


def test_quit_is_valid_on_a_page_with_no_options():
    page = make_page(0)
    assert parse_pick("q", page) == Option(key="q", kind="quit", summary=None)


def test_empty_and_whitespace_input_is_invalid():
    page = make_page(3)
    assert parse_pick("", page) is None
    assert parse_pick("   ", page) is None


def test_zero_and_out_of_range_numbers_are_invalid():
    page = make_page(3)
    assert parse_pick("0", page) is None
    assert parse_pick("4", page) is None
    assert parse_pick("99", page) is None


def test_non_digit_garbage_is_invalid():
    page = make_page(3, more_available=True)
    assert parse_pick("x", page) is None
    assert parse_pick("1x", page) is None
    assert parse_pick("-1", page) is None


def test_letter_picks_are_case_insensitive():
    page = make_page(3, more_available=True)
    assert parse_pick("Q", page) == Option(key="q", kind="quit", summary=None)
    assert parse_pick("M", page) == Option(key="m", kind="more", summary=None)


def test_input_whitespace_is_stripped_before_matching():
    page = make_page(3, more_available=False)
    assert parse_pick(" 2 ", page) == page.options[1]
    assert parse_pick(" q ", page) == Option(key="q", kind="quit", summary=None)


def test_parse_pick_is_deterministic_across_equal_pages():
    # Separately built but equal inputs must yield equal picks.
    assert parse_pick("2", make_page(3)) == parse_pick("2", make_page(3))


# --- build_parent_chain ------------------------------------------------


def test_no_declared_parent_yields_empty_chain():
    calls = []
    chain = build_parent_chain("acme/original", None, make_fetch({}, calls))
    assert chain == []
    assert calls == []


def test_single_parent_hop_records_ok_link():
    parent = summary("acme/base")
    chain = build_parent_chain("q/tiny-GGUF", "acme/base", make_fetch({"acme/base": parent}))
    assert chain == [ParentLink(requested_id="acme/base", summary=parent, status="ok")]


def test_walks_repeated_hops_nearest_parent_first():
    base = summary("acme/base", base_model="acme/root")
    root = summary("acme/root")
    fetch = make_fetch({"acme/base": base, "acme/root": root})
    chain = build_parent_chain("q/tiny-GGUF", "acme/base", fetch)
    assert chain == [
        ParentLink(requested_id="acme/base", summary=base, status="ok"),
        ParentLink(requested_id="acme/root", summary=root, status="ok"),
    ]


def test_missing_parent_records_not_found_link():
    chain = build_parent_chain("q/tiny-GGUF", "gone/base", make_fetch({}))
    assert chain == [ParentLink(requested_id="gone/base", summary=None, status="not-found")]


def test_not_found_mid_chain_keeps_earlier_links_and_stops():
    base = summary("acme/base", base_model="gone/root")
    calls = []
    chain = build_parent_chain("q/tiny-GGUF", "acme/base", make_fetch({"acme/base": base}, calls))
    assert chain == [
        ParentLink(requested_id="acme/base", summary=base, status="ok"),
        ParentLink(requested_id="gone/root", summary=None, status="not-found"),
    ]
    assert calls == ["acme/base", "gone/root"]


def test_renamed_parent_is_marked_and_chain_follows_current_id():
    renamed = summary("new/base", base_model="acme/grand")
    grand = summary("acme/grand")
    fetch = make_fetch({"old/base": renamed, "acme/grand": grand})
    chain = build_parent_chain("q/tiny-GGUF", "old/base", fetch)
    assert chain == [
        ParentLink(requested_id="old/base", summary=renamed, status="renamed"),
        ParentLink(requested_id="acme/grand", summary=grand, status="ok"),
    ]


def test_two_repo_cycle_stops_after_visiting_each_repo_once():
    parent_a = summary("acme/parent-a", base_model="acme/parent-b")
    parent_b = summary("acme/parent-b", base_model="acme/parent-a")
    calls = []
    fetch = make_fetch({"acme/parent-a": parent_a, "acme/parent-b": parent_b}, calls)
    chain = build_parent_chain("acme/child", "acme/parent-a", fetch)
    assert chain == [
        ParentLink(requested_id="acme/parent-a", summary=parent_a, status="ok"),
        ParentLink(requested_id="acme/parent-b", summary=parent_b, status="ok"),
    ]
    assert calls == ["acme/parent-a", "acme/parent-b"]


def test_parent_pointing_back_at_starting_repo_stops():
    parent = summary("acme/parent", base_model="acme/child")
    calls = []
    fetch = make_fetch({"acme/parent": parent}, calls)
    chain = build_parent_chain("acme/child", "acme/parent", fetch)
    assert chain == [ParentLink(requested_id="acme/parent", summary=parent, status="ok")]
    assert calls == ["acme/parent"]


def test_build_parent_chain_is_deterministic():
    def run():
        base = summary("acme/base", base_model="acme/root")
        root = summary("acme/root")
        fetch = make_fetch({"acme/base": base, "acme/root": root})
        return build_parent_chain("q/tiny-GGUF", "acme/base", fetch)

    assert run() == run()
