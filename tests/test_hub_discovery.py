"""Tests for llm_preserver.hub_discovery — spec 0006, Phase A (hub seam).

Pins the pure discovery pieces: the ``HubPager`` cursor over a lazy
hub iterator (no total is available — the hub paginates by cursor, so
the pager exposes only ``fetched``/``exhausted``), the frozen
``ModelSummary`` row, the ``RelationType`` vocabulary, and the
``FakeHubClient`` conformance for the three new protocol methods
(canned data served verbatim through a real ``HubPager``; fault-domain
errors match the real client's mapping).

The real ``HubClient`` counterparts live in
``test_hub_discovery_client.py``.
"""

from typing import get_args

import pytest

import llm_preserver.hub as hub
from llm_preserver.hub_discovery import PAGE_SIZE, HubPager, ModelSummary, RelationType


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


# --- HubPager ---------------------------------------------------------


def test_page_size_constant_is_twenty():
    assert PAGE_SIZE == 20


def test_returns_full_pages_of_requested_page_size():
    pager = HubPager(iter([summary(f"acme/m{i}") for i in range(10)]), page_size=4)
    assert [s.repo_id for s in pager.next_page()] == ["acme/m0", "acme/m1", "acme/m2", "acme/m3"]
    assert [s.repo_id for s in pager.next_page()] == ["acme/m4", "acme/m5", "acme/m6", "acme/m7"]


def test_default_page_size_is_module_constant():
    pager = HubPager(iter([summary(f"acme/m{i}") for i in range(PAGE_SIZE + 5)]))
    assert len(pager.next_page()) == PAGE_SIZE


def test_short_final_page_carries_remainder_and_marks_exhausted():
    pager = HubPager(iter([summary(f"acme/m{i}") for i in range(10)]), page_size=4)
    pager.next_page()
    pager.next_page()
    final = pager.next_page()
    assert [s.repo_id for s in final] == ["acme/m8", "acme/m9"]
    assert pager.exhausted is True


def test_exact_multiple_source_returns_empty_page_then_exhausted():
    pager = HubPager(iter([summary(f"acme/m{i}") for i in range(4)]), page_size=4)
    assert len(pager.next_page()) == 4
    assert pager.next_page() == []
    assert pager.exhausted is True


def test_exhausted_stays_true_and_pages_stay_empty_on_repeated_calls():
    pager = HubPager(iter([summary("acme/m0")]), page_size=4)
    pager.next_page()
    assert pager.next_page() == []
    assert pager.next_page() == []
    assert pager.exhausted is True


def test_fetched_counts_total_items_returned_so_far():
    pager = HubPager(iter([summary(f"acme/m{i}") for i in range(10)]), page_size=4)
    pager.next_page()
    assert pager.fetched == 4
    pager.next_page()
    assert pager.fetched == 8
    pager.next_page()
    assert pager.fetched == 10
    pager.next_page()
    assert pager.fetched == 10


def test_empty_source_is_exhausted_after_first_empty_page():
    pager = HubPager(iter([]))
    assert pager.next_page() == []
    assert pager.exhausted is True


def test_fresh_pager_over_unread_source_is_not_exhausted():
    pager = HubPager(iter([summary("acme/m0")]))
    assert pager.exhausted is False


# --- ModelSummary / RelationType --------------------------------------


def test_model_summary_is_frozen():
    row = summary("acme/tiny-chat")
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        row.downloads = 1  # type: ignore[misc]


def test_model_summary_relation_defaults_to_none():
    assert summary("acme/tiny-chat").relation is None


def test_relation_type_names_the_four_tree_relations():
    assert set(get_args(RelationType)) == {"quantized", "finetune", "adapter", "merge"}


# --- FakeHubClient conformance ----------------------------------------


def test_fake_search_returns_canned_results_in_canned_order(fake_hub_factory):
    # Deliberately anti-sorted by id and downloads: order must be verbatim.
    canned = [summary("z/last-alpha", downloads=5), summary("a/first-alpha", downloads=999)]
    fake = fake_hub_factory(search_results=canned)
    page = fake.search_models("tiny chat").next_page()
    assert [s.repo_id for s in page] == ["z/last-alpha", "a/first-alpha"]


def test_fake_list_children_returns_only_that_relation_of_that_repo(fake_hub_factory):
    fake = fake_hub_factory(
        children={
            ("acme/tiny-chat", "quantized"): [summary("q/tiny-chat-GGUF", relation="quantized")],
            ("acme/tiny-chat", "finetune"): [summary("f/tiny-chat-ft", relation="finetune")],
            ("other/base", "quantized"): [summary("q/other-GGUF", relation="quantized")],
        }
    )
    page = fake.list_children("acme/tiny-chat", "quantized").next_page()
    assert [s.repo_id for s in page] == ["q/tiny-chat-GGUF"]


def test_fake_model_summary_returns_canned_summary(fake_hub_factory):
    canned = summary("acme/tiny-chat", downloads=7, gated="auto")
    fake = fake_hub_factory(summaries={"acme/tiny-chat": canned})
    assert fake.model_summary("acme/tiny-chat") == canned


def test_fake_model_summary_unknown_repo_raises_user_error(fake_hub_factory):
    fake = fake_hub_factory(summaries={})
    with pytest.raises(hub.PullUserError):
        fake.model_summary("acme/does-not-exist")


def test_fake_search_raises_injected_hub_error(fake_hub_factory):
    fake = fake_hub_factory(search_error=hub.PullHubError("hub is rate limiting"))
    with pytest.raises(hub.PullHubError):
        fake.search_models("tiny chat")


def test_fake_list_children_raises_injected_env_error(fake_hub_factory):
    fake = fake_hub_factory(list_children_error=hub.PullEnvError("network failure"))
    with pytest.raises(hub.PullEnvError):
        fake.list_children("acme/tiny-chat", "quantized")


def test_fake_model_summary_raises_injected_hub_error(fake_hub_factory):
    fake = fake_hub_factory(model_summary_error=hub.PullHubError("hub-side failure"))
    with pytest.raises(hub.PullHubError):
        fake.model_summary("acme/tiny-chat")
