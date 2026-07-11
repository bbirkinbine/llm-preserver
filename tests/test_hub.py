"""Tests for llm_preserver.hub — hub-exception → fault-domain mapping.

Builds real ``huggingface_hub.errors`` instances (with httpx
request/response context, never touching the network) and pins
``map_hub_exception``: it returns an instance of the fault-domain
exception (``PullUserError`` / ``PullEnvError`` / ``PullHubError`` /
``PullIntegrityError``) that pull orchestration and the CLI key on.
Mapping is by typed exception class and status code, never by message
string (spec 0003, Notes → Logging).
"""

import httpx
from huggingface_hub import errors as hf_errors

import llm_preserver.hub as hub

API_URL = "https://huggingface.co/api/models/acme/tiny-chat"


def hf_http_error(status_code, cls=hf_errors.HfHubHTTPError, message="boom"):
    """Build a real hub HTTP error carrying an httpx response context."""
    response = httpx.Response(status_code, request=httpx.Request("GET", API_URL))
    return cls(message, response=response)


def test_repository_not_found_maps_to_user_error():
    mapped = hub.map_hub_exception(hf_http_error(404, hf_errors.RepositoryNotFoundError))
    assert isinstance(mapped, hub.PullUserError)


def test_unauthorized_lookup_maps_to_user_error():
    # 401 surfaces as RepositoryNotFoundError by huggingface_hub design
    # (spec 0003, External references).
    mapped = hub.map_hub_exception(hf_http_error(401, hf_errors.RepositoryNotFoundError))
    assert isinstance(mapped, hub.PullUserError)


def test_gated_repo_maps_to_user_error():
    mapped = hub.map_hub_exception(hf_http_error(403, hf_errors.GatedRepoError))
    assert isinstance(mapped, hub.PullUserError)


def test_revision_not_found_maps_to_user_error():
    mapped = hub.map_hub_exception(hf_http_error(404, hf_errors.RevisionNotFoundError))
    assert isinstance(mapped, hub.PullUserError)


def test_missing_remote_file_maps_to_user_error():
    # RemoteEntryNotFoundError is the 1.x HTTP file-404 error.
    mapped = hub.map_hub_exception(hf_http_error(404, hf_errors.RemoteEntryNotFoundError))
    assert isinstance(mapped, hub.PullUserError)


def test_rate_limiting_maps_to_hub_error():
    # No dedicated class upstream: 429 rides plain HfHubHTTPError.
    mapped = hub.map_hub_exception(hf_http_error(429))
    assert isinstance(mapped, hub.PullHubError)


def test_server_5xx_maps_to_hub_error():
    mapped = hub.map_hub_exception(hf_http_error(503))
    assert isinstance(mapped, hub.PullHubError)


def test_connect_error_maps_to_env_error():
    mapped = hub.map_hub_exception(httpx.ConnectError("connection refused"))
    assert isinstance(mapped, hub.PullEnvError)


def test_transport_error_maps_to_env_error():
    mapped = hub.map_hub_exception(httpx.TransportError("network unreachable"))
    assert isinstance(mapped, hub.PullEnvError)


def test_fault_domain_classes_are_four_distinct_types():
    domains = {hub.PullUserError, hub.PullEnvError, hub.PullHubError, hub.PullIntegrityError}
    assert len(domains) == 4
