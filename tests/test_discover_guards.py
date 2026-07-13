"""Hardening pins for discovery internals (spec 0006 review round).

Hub metadata is untrusted: malformed ``base_model`` values must never
become request parameters or unmapped crashes, and hostile chain
lengths must not turn tree entry into an unbounded call storm.
"""

from types import SimpleNamespace

from llm_preserver.discover import MAX_PARENT_HOPS, build_parent_chain
from llm_preserver.hub_discovery import ModelSummary, summarize


def summary(repo_id, **overrides):
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def test_malformed_parent_id_is_never_fetched():
    # A traversal-shaped base_model must not become a request path
    # component; it renders like a stale parent instead.
    calls = []

    def fetch(repo_id):
        calls.append(repo_id)
        raise AssertionError("must not be called")

    chain = build_parent_chain("acme/tiny", "../../whoami-v2?x=", fetch)

    assert calls == []
    assert [link.status for link in chain] == ["not-found"]
    assert chain[0].requested_id == "../../whoami-v2?x="


def test_single_segment_legacy_repo_id_is_fetched():
    # Legacy hub ids like "gpt2" have no namespace and are valid.
    chain = build_parent_chain("acme/tiny", "gpt2", lambda repo_id: summary("gpt2"))

    assert [link.status for link in chain] == ["ok"]


def test_parent_chain_depth_is_capped():
    # A hostile 50-repo chain must not fire 50 calls on tree entry.
    calls = []

    def fetch(repo_id):
        calls.append(repo_id)
        index = int(repo_id.split("/m")[1])
        return summary(repo_id, base_model=f"acme/m{index + 1}")

    chain = build_parent_chain("acme/m0", "acme/m1", fetch)

    assert len(chain) == MAX_PARENT_HOPS
    assert len(calls) == MAX_PARENT_HOPS


def test_summarize_survives_base_models_list_of_strings():
    model = SimpleNamespace(
        id="acme/tiny",
        downloads=1,
        last_modified=None,
        gated=False,
        base_models={"relation": "quantized", "models": ["acme/base"]},
    )

    assert summarize(model).base_model is None


def test_summarize_survives_non_string_base_model_id():
    model = SimpleNamespace(
        id="acme/tiny",
        downloads=1,
        last_modified=None,
        gated=False,
        base_models={"relation": "quantized", "models": [{"id": 123}]},
    )

    assert summarize(model).base_model is None
