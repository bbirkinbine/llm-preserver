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
from huggingface_hub.utils import validate_repo_id

import llm_preserver.hub as hub
from llm_preserver.hub.errors import MAPPED_EXCEPTIONS

API_URL = "https://huggingface.co/api/models/acme/tiny-chat"

# Spec 0011: an Ollama-style ``name:tag`` reference pasted where the tool
# wants a hub ``org/name`` id. The ``:`` fails the library's
# ``validate_repo_id``, which raises ``HFValidationError`` (a ValueError
# subclass — verified 2026-07-15).
INVALID_REPO_ID = "qwen3-vl:30b-a3b-instruct"


def hf_http_error(status_code, cls=hf_errors.HfHubHTTPError, message="boom"):
    """Build a real hub HTTP error carrying an httpx response context."""
    response = httpx.Response(status_code, request=httpx.Request("GET", API_URL))
    return cls(message, response=response)


def hf_validation_error(repo_id=INVALID_REPO_ID):
    """Return the real ``HFValidationError`` the library raises for a bad id.

    Drives the installed ``validate_repo_id`` so the exact class and
    message the hub client would see flow through ``map_hub_exception`` —
    never a stand-in double (spec 0011, External references).
    """
    try:
        validate_repo_id(repo_id)
    except hf_errors.HFValidationError as exc:
        return exc
    raise AssertionError(f"{repo_id!r} unexpectedly passed validate_repo_id")


def test_invalid_repo_id_maps_to_user_error():
    # An invalid repo id is a user-input fault (exit 2), not a local
    # environment failure (spec 0011). Today it escapes the typed
    # branches and falls through to PullEnvError, so this is the red.
    mapped = hub.map_hub_exception(hf_validation_error())
    assert isinstance(mapped, hub.PullUserError)


def test_invalid_repo_id_error_names_input_and_points_at_discover():
    # The clean message must name the offending input and route the user
    # to the recovery path — search the hub by name with `discover`
    # (spec 0011). Today's fall-through message carries no such pointer.
    message = str(hub.map_hub_exception(hf_validation_error()))
    assert INVALID_REPO_ID in message
    assert "discover" in message.lower()


def test_hf_validation_error_is_in_mapped_exceptions():
    # The hub client only maps what its ``except MAPPED_EXCEPTIONS``
    # catches; HFValidationError must be a member of that tuple or it
    # escapes unmapped and Typer prints a Traceback (spec 0011).
    assert hf_errors.HFValidationError in MAPPED_EXCEPTIONS


def test_valid_repo_ids_never_trip_the_validator():
    # No false negatives (spec 0011): ids the hub accepts — canonical
    # single-component, namespaced, and ``.``/``-``/``_`` bearing — must
    # not raise HFValidationError, so map_hub_exception is never reached
    # for them and the pull proceeds to the network unchanged.
    for good in ("gpt2", "Qwen/Qwen3-VL-30B-A3B-Instruct", "acme/tiny.chat_v1-2"):
        validate_repo_id(good)  # raises HFValidationError if this regresses


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
