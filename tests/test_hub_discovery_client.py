"""Tests for the real HubClient discovery methods — spec 0006, Phase A.

Pins ``search_models`` / ``list_children`` / ``model_summary`` on
``llm_preserver.hub.HubClient``: verbatim hub ordering (search passes
NO sort — the hub's relevance order stands), the children filter
``base_model:<relation>:<repo_id>`` with ``sort="downloads"``, field
normalization into ``ModelSummary`` (``gated`` False → None, datetime →
ISO string, first ``baseModels`` id), and the existing fault-domain
mapping for a repo 404.

Tests replace ``llm_preserver.hub.HfApi`` with a recording fake before
constructing ``HubClient`` — zero network. Hub API facts (attribute
names, ``expand`` field names, filter syntax, the ``baseModels`` shape
``{"relation": ..., "models": [{"id": ...}]}`` landing on
``ModelInfo.base_models``) were live-verified 2026-07-13 against
huggingface_hub 1.23.0 and are encoded here, not re-verified.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from huggingface_hub import errors as hf_errors

import llm_preserver.hub as hub
from llm_preserver.hub import client as hub_client

API_URL = "https://huggingface.co/api/models/acme/tiny-chat"


def hf_http_error(status_code, cls=hf_errors.HfHubHTTPError, message="boom"):
    """Build a real hub HTTP error carrying an httpx response context."""
    response = httpx.Response(status_code, request=httpx.Request("GET", API_URL))
    return cls(message, response=response)


def hub_model(repo_id, *, downloads=100, last_modified=None, gated=False, base_models=None):
    """Fake hub listing/info object with the live-verified attributes."""
    return SimpleNamespace(
        id=repo_id,
        downloads=downloads,
        last_modified=last_modified,
        gated=gated,
        base_models=base_models,
    )


class RecordingApi:
    """Stands in for ``HfApi``: records call kwargs, serves canned data."""

    def __init__(self, models=(), info=None, info_error=None):
        self.models = list(models)
        self.info = info
        self.info_error = info_error
        self.list_models_calls: list[dict] = []
        self.model_info_calls: list[tuple] = []

    def list_models(self, **kwargs):
        self.list_models_calls.append(kwargs)
        return iter(self.models)

    def model_info(self, repo_id, **kwargs):
        self.model_info_calls.append((repo_id, kwargs))
        if self.info_error is not None:
            raise self.info_error
        return self.info


@pytest.fixture
def real_client(monkeypatch):
    """Factory: a real ``HubClient`` wired to a ``RecordingApi``."""

    def make(api):
        # The seam moved into the hub package's client module when
        # hub.py split (300-line cap); HubClient resolves HfApi there.
        monkeypatch.setattr(hub_client, "HfApi", lambda: api)
        return hub.HubClient()

    return make


# --- search_models ----------------------------------------------------


def test_search_passes_query_verbatim_and_requests_no_sort(real_client):
    api = RecordingApi(models=[hub_model("acme/tiny-chat")])
    real_client(api).search_models("qwen coder").next_page()
    call = api.list_models_calls[0]
    assert call["search"] == "qwen coder"
    assert call.get("sort") is None  # hub relevance order, never ours


def test_search_requests_the_discovery_expand_fields(real_client):
    api = RecordingApi(models=[hub_model("acme/tiny-chat")])
    real_client(api).search_models("tiny").next_page()
    expand = set(api.list_models_calls[0]["expand"])
    assert {"downloads", "lastModified", "gated", "baseModels"} <= expand


def test_search_preserves_hub_result_order(real_client):
    api = RecordingApi(models=[hub_model("z/low", downloads=1), hub_model("a/high", downloads=999)])
    page = real_client(api).search_models("tiny").next_page()
    assert [s.repo_id for s in page] == ["z/low", "a/high"]


def test_search_normalizes_gated_false_to_none(real_client):
    api = RecordingApi(models=[hub_model("acme/tiny-chat", gated=False)])
    page = real_client(api).search_models("tiny").next_page()
    assert page[0].gated is None


@pytest.mark.parametrize("gated", ["auto", "manual"])
def test_search_passes_gated_marker_strings_through(real_client, gated):
    api = RecordingApi(models=[hub_model("acme/tiny-chat", gated=gated)])
    page = real_client(api).search_models("tiny").next_page()
    assert page[0].gated == gated


def test_search_converts_last_modified_datetime_to_iso_string(real_client):
    modified = datetime(2026, 7, 1, 12, 30, tzinfo=UTC)
    api = RecordingApi(models=[hub_model("acme/tiny-chat", last_modified=modified)])
    page = real_client(api).search_models("tiny").next_page()
    assert isinstance(page[0].last_modified, str)
    assert datetime.fromisoformat(page[0].last_modified) == modified


def test_search_missing_downloads_and_last_modified_become_none(real_client):
    api = RecordingApi(models=[hub_model("acme/tiny-chat", downloads=None, last_modified=None)])
    page = real_client(api).search_models("tiny").next_page()
    assert page[0].downloads is None
    assert page[0].last_modified is None


@pytest.mark.parametrize(
    ("base_models", "expected"),
    [
        ({"relation": "quantized", "models": [{"id": "acme/base"}]}, "acme/base"),
        (
            {"relation": "quantized", "models": [{"id": "acme/first"}, {"id": "acme/second"}]},
            "acme/first",
        ),
        ({"relation": "finetune", "models": []}, None),
        (None, None),
    ],
)
def test_search_extracts_first_declared_base_model_id(real_client, base_models, expected):
    api = RecordingApi(models=[hub_model("acme/tiny-chat", base_models=base_models)])
    page = real_client(api).search_models("tiny").next_page()
    assert page[0].base_model == expected


# --- list_children ----------------------------------------------------


def test_list_children_builds_typed_base_model_filter(real_client):
    api = RecordingApi(models=[hub_model("q/tiny-chat-GGUF")])
    real_client(api).list_children("acme/tiny-chat", "quantized").next_page()
    assert api.list_models_calls[0]["filter"] == "base_model:quantized:acme/tiny-chat"


def test_list_children_requests_hub_download_sort(real_client):
    api = RecordingApi(models=[hub_model("q/tiny-chat-GGUF")])
    real_client(api).list_children("acme/tiny-chat", "quantized").next_page()
    assert api.list_models_calls[0]["sort"] == "downloads"


def test_list_children_rows_carry_the_listing_relation(real_client):
    base_models = {"relation": "finetune", "models": [{"id": "acme/tiny-chat"}]}
    api = RecordingApi(models=[hub_model("f/tiny-chat-ft", base_models=base_models)])
    page = real_client(api).list_children("acme/tiny-chat", "finetune").next_page()
    assert page[0].relation == "finetune"


# --- model_summary ----------------------------------------------------


def test_model_summary_maps_repo_404_to_user_error(real_client):
    api = RecordingApi(info_error=hf_http_error(404, hf_errors.RepositoryNotFoundError))
    with pytest.raises(hub.PullUserError):
        real_client(api).model_summary("acme/does-not-exist")


def test_model_summary_returns_post_rename_repo_id(real_client):
    # The hub redirects renamed repos; the client surfaces the returned
    # id, no error — the caller compares against what it asked for.
    api = RecordingApi(info=hub_model("qwen/new-name"))
    result = real_client(api).model_summary("qwen/old-name")
    assert result.repo_id == "qwen/new-name"


def test_model_summary_normalizes_fields_like_search(real_client):
    modified = datetime(2026, 7, 2, tzinfo=UTC)
    api = RecordingApi(
        info=hub_model("acme/tiny-chat", downloads=42, last_modified=modified, gated=False)
    )
    result = real_client(api).model_summary("acme/tiny-chat")
    assert result.downloads == 42
    assert result.gated is None
    assert datetime.fromisoformat(result.last_modified) == modified
