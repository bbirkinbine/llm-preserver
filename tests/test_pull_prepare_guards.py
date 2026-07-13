"""Hardening tests for the advisory-only adapter_config.json fetch.

Spec 0005 adjudication: the fetch exists so the adapter-base advisory
is accurate, and hub data is untrusted — no shape of bad config
(malformed, non-object, oversized, unfetchable, nested decoy) may
abort a pull or trigger a fetch it shouldn't. FakeHubClient from
conftest; no network.
"""

import pytest

from llm_preserver.archive import init_archive
from llm_preserver.hub import PullHubError
from llm_preserver.pull_prepare import prepare_pull

REPO_ID = "acme/tiny-adapter"


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def adapter_files(config_bytes, config_path="adapter_config.json"):
    return [
        ("adapter_model.safetensors", b"adapter weight bytes", True),
        (config_path, config_bytes, False),
    ]


def prepare(archive, client):
    return prepare_pull(
        archive,
        REPO_ID,
        client,
        include=["adapter_model*"],
        model="acme/tiny-adapter",
        confirm=lambda prompt: True,
    )


def advisory_kinds(prep):
    return [advisory.kind for advisory in prep.advisories]


def test_valid_adapter_config_yields_base_model_advisory(archive, fake_hub_factory):
    client = fake_hub_factory(
        files=adapter_files(b'{"base_model_name_or_path": "acme/base-7b"}'),
        repo_id=REPO_ID,
        base_model=None,
    )

    prep = prepare(archive, client)

    assert "adapter base model" in advisory_kinds(prep)
    assert client.download_calls == ["adapter_config.json"]


def test_non_object_json_config_yields_no_advisory_and_no_error(archive, fake_hub_factory):
    # Valid JSON that is not an object must not raise (a hub repo
    # controls this file entirely).
    client = fake_hub_factory(
        files=adapter_files(b'["not", "a", "dict"]'), repo_id=REPO_ID, base_model=None
    )

    prep = prepare(archive, client)

    assert "adapter base model" not in advisory_kinds(prep)


def test_malformed_json_config_yields_no_advisory_and_no_error(archive, fake_hub_factory):
    client = fake_hub_factory(files=adapter_files(b"{not json"), repo_id=REPO_ID, base_model=None)

    prep = prepare(archive, client)

    assert "adapter base model" not in advisory_kinds(prep)


def test_failed_config_download_never_aborts_the_pull(archive, fake_hub_factory):
    # A rate-limit or 5xx on the advisory-only fetch must not kill the
    # pull it was trying to help.
    client = fake_hub_factory(
        files=adapter_files(b'{"base_model_name_or_path": "acme/base-7b"}'),
        repo_id=REPO_ID,
        base_model=None,
        download_error=PullHubError("hub says 500"),
    )

    prep = prepare(archive, client)

    assert "adapter base model" not in advisory_kinds(prep)


def test_oversized_config_is_never_downloaded(archive, fake_hub_factory):
    # An "adapter config" at megabyte scale is not worth fetching for
    # an advisory — resource exhaustion via a hostile repo.
    client = fake_hub_factory(
        files=adapter_files(b"x" * (1024 * 1024 + 1)), repo_id=REPO_ID, base_model=None
    )

    prep = prepare(archive, client)

    assert client.download_calls == []
    assert "adapter base model" not in advisory_kinds(prep)


def test_nested_config_is_not_fetched(archive, fake_hub_factory):
    # peft writes adapter_config.json at the repo root; a nested decoy
    # must not steer which config gets read (root-only match).
    client = fake_hub_factory(
        files=adapter_files(
            b'{"base_model_name_or_path": "evil/decoy"}',
            config_path="nested/adapter_config.json",
        ),
        repo_id=REPO_ID,
        base_model=None,
    )

    prep = prepare(archive, client)

    assert client.download_calls == []
    assert "adapter base model" not in advisory_kinds(prep)
