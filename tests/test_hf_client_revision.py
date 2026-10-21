"""The real ``HubClient`` on the ``--revision`` path (spec 0024).

Offline, like ``test_hf_client_compatibility.py``: a real public
``ModelInfo`` flows through the production ``HubClient`` while only
``HfApi`` is replaced by a recorder. Pins criteria 2, 6, 8, 12 and 18 at
the one place the library is called.

Expected red (test-first): ``HubClient.repo_info`` takes no
``revision`` argument yet, so every revision call is a TypeError.
"""

import httpx
import pytest
from huggingface_hub import ModelInfo
from huggingface_hub import errors as hf_errors

import llm_preserver.hub as hub
from llm_preserver.hub import client as hub_client

REPO_ID = "turboderp/tiny-exl3"
BRANCH_SHA = "2a30229e67012798ba9f0cd832bb78abf4c363d5"
OTHER_SHA = "ef396bc0" + "0" * 32


def model_info(sha: str | None) -> ModelInfo:
    """A minimal real ModelInfo whose ``sha`` is the hub's answer."""
    return ModelInfo(
        id=REPO_ID,
        sha=sha,
        siblings=[{"rfilename": "config.json", "size": 2}],
    )


class RecordingInfoApi:
    """Return one real ``ModelInfo`` while recording the production call."""

    def __init__(self, info: ModelInfo) -> None:
        self.info = info
        self.calls: list[tuple[str, dict[str, object]]] = []

    def model_info(self, repo_id: str, **kwargs: object) -> ModelInfo:
        self.calls.append((repo_id, kwargs))
        return self.info


class RaisingInfoApi:
    """``model_info`` raises the hub exception under test."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def model_info(self, repo_id: str, **kwargs: object) -> ModelInfo:
        raise self.exc


def install_api(monkeypatch: pytest.MonkeyPatch, api: object) -> None:
    monkeypatch.setattr(hub_client, "HfApi", lambda: api)


def test_revision_kwarg_is_sent_only_when_a_revision_was_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Differential: the default call stays byte-identical to today
    # (criterion 2) while the revision call carries revision= (criterion 6).
    api = RecordingInfoApi(model_info(BRANCH_SHA))
    install_api(monkeypatch, api)
    client = hub.HubClient()

    client.repo_info(REPO_ID)
    client.repo_info(REPO_ID, revision="4.05bpw")

    assert api.calls == [
        (REPO_ID, {"files_metadata": True}),
        (REPO_ID, {"files_metadata": True, "revision": "4.05bpw"}),
    ]


@pytest.mark.parametrize("ref", ["4.05bpw", "v1.0", "feature/q4", "refs/pr/3"])
def test_branch_tag_and_pr_refs_pass_through_verbatim(
    monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    # Criterion 12: no splitting on "/", no quoting here — the library
    # URL-quotes the revision itself.
    api = RecordingInfoApi(model_info(BRANCH_SHA))
    install_api(monkeypatch, api)

    result = hub.HubClient().repo_info(REPO_ID, revision=ref)

    assert api.calls == [(REPO_ID, {"files_metadata": True, "revision": ref})]
    assert result.commit == BRANCH_SHA


def test_a_full_sha_request_resolves_to_that_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    api = RecordingInfoApi(model_info(BRANCH_SHA))
    install_api(monkeypatch, api)

    result = hub.HubClient().repo_info(REPO_ID, revision=BRANCH_SHA)

    assert result.commit == BRANCH_SHA


def test_an_uppercase_full_sha_request_matches_the_hubs_lowercase_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Plan decision 4: an uppercase 40-hex request counts as a SHA and is
    # compared lowercased; the record carries the hub's lowercase form.
    api = RecordingInfoApi(model_info(BRANCH_SHA))
    install_api(monkeypatch, api)

    result = hub.HubClient().repo_info(REPO_ID, revision=BRANCH_SHA.upper())

    assert result.commit == BRANCH_SHA


@pytest.mark.parametrize(
    "returned",
    ["2a30229", BRANCH_SHA.upper(), "g" * 40, BRANCH_SHA + "0", BRANCH_SHA[:-1]],
)
def test_a_returned_sha_not_40_lowercase_hex_is_a_hub_fault(
    monkeypatch: pytest.MonkeyPatch, returned: str
) -> None:
    # Criterion 8: a malformed commit must never become a record pin.
    install_api(monkeypatch, RecordingInfoApi(model_info(returned)))

    with pytest.raises(hub.PullHubError):
        hub.HubClient().repo_info(REPO_ID, revision="4.05bpw")


def test_a_full_sha_request_answered_with_another_sha_is_a_hub_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Criterion 8: asking for commit X and being handed commit Y would
    # archive a tree the human did not ask for.
    install_api(monkeypatch, RecordingInfoApi(model_info(OTHER_SHA)))

    with pytest.raises(hub.PullHubError):
        hub.HubClient().repo_info(REPO_ID, revision=BRANCH_SHA)


def test_a_missing_revision_is_a_user_fault_not_an_invalid_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Criterion 18: exit 2 through the existing mapping, but never the
    # Ollama-shape hint carrier (the id itself was fine).
    response = httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co/api"))
    exc = hf_errors.RevisionNotFoundError("Revision Not Found", response=response)
    install_api(monkeypatch, RaisingInfoApi(exc))

    with pytest.raises(hub.PullUserError) as caught:
        hub.HubClient().repo_info(REPO_ID, revision="no-such-branch")

    assert not isinstance(caught.value, hub.PullInvalidIdError)
