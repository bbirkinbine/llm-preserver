"""Local Ollama store reader — spec 0013 phase A, unit level.

``parse_ollama_name`` normalizes an Ollama model name into the four
manifest-path components (registry/namespace/model/tag) with Ollama's
own defaults filled in; ``local_model_digests`` reads the model-layer
SHA256 digests from the local manifest file. Read-only, no network, no
Ollama server — every store here is a fake built in ``tmp_path``.

Layout constants come from ``llm_preserver.ollama_layout`` (already
implemented, provenance-pinned there); the reader under test lives in
the new ``llm_preserver.ollama_store`` module.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from llm_preserver import ollama_layout
from llm_preserver.ollama_store import (
    OllamaName,
    OllamaStoreError,
    local_model_digests,
    parse_ollama_name,
)

MODEL_MEDIA_TYPE = "application/vnd.ollama.image.model"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def model_layer(digest: str, media_type: str = MODEL_MEDIA_TYPE, size: int = 100) -> dict:
    return {"mediaType": media_type, "digest": f"sha256:{digest}", "size": size}


def write_manifest(
    store_root: Path,
    name: OllamaName,
    layers: list[dict] | None = None,
    *,
    raw: str | None = None,
) -> Path:
    manifest = (
        store_root
        / ollama_layout.MANIFESTS_DIRNAME
        / name.registry
        / name.namespace
        / name.model
        / name.tag
    )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    text = raw if raw is not None else json.dumps({"schemaVersion": 2, "layers": layers})
    manifest.write_text(text, encoding="utf-8")
    return manifest


# --- parse_ollama_name ---------------------------------------------------


def test_bare_name_fills_ollama_defaults():
    assert parse_ollama_name("bge-m3") == OllamaName(
        registry=ollama_layout.REGISTRY_DIRNAME,
        namespace=ollama_layout.DEFAULT_NAMESPACE,
        model="bge-m3",
        tag=ollama_layout.DEFAULT_TAG,
    )


def test_explicit_tag_replaces_default_tag():
    name = parse_ollama_name("bge-m3:q8_0")
    assert name.model == "bge-m3"
    assert name.tag == "q8_0"


def test_two_segments_set_the_namespace():
    name = parse_ollama_name("creator/model:tag")
    assert name.registry == ollama_layout.REGISTRY_DIRNAME
    assert name.namespace == "creator"
    assert name.model == "model"
    assert name.tag == "tag"


def test_three_segments_set_the_registry():
    name = parse_ollama_name("hf.co/org/repo:Q4_K_M")
    assert name == OllamaName(registry="hf.co", namespace="org", model="repo", tag="Q4_K_M")


def test_parsed_name_is_immutable():
    name = parse_ollama_name("bge-m3")
    with pytest.raises(dataclasses.FrozenInstanceError):
        name.tag = "other"  # type: ignore[misc]


def test_empty_name_raises_store_error():
    with pytest.raises(OllamaStoreError):
        parse_ollama_name("")


@pytest.mark.parametrize(
    "bad",
    [
        ":tag",
        "a//b",
        "a/b/c/d",
        # Safe-alphabet gate (security round 2026-07-31): `..` and `/`
        # must never become manifest path segments, and a leading dash
        # must never reach pasteable hint text as a flag-shaped token.
        "m:../../x",
        "..",
        "--yes:latest",
        "-h:latest",
    ],
)
def test_bad_name_raises_store_error_naming_the_input(bad):
    with pytest.raises(OllamaStoreError) as excinfo:
        parse_ollama_name(bad)
    assert bad in str(excinfo.value)


# --- local_model_digests -------------------------------------------------


def test_returns_bare_hex_digest_of_the_model_layer(tmp_path):
    name = parse_ollama_name("bge-m3")
    write_manifest(tmp_path, name, [model_layer(DIGEST_A)])

    assert local_model_digests(name, tmp_path) == [DIGEST_A]


def test_returns_every_model_layer_digest(tmp_path):
    name = parse_ollama_name("bge-m3")
    write_manifest(tmp_path, name, [model_layer(DIGEST_A), model_layer(DIGEST_B)])

    assert local_model_digests(name, tmp_path) == [DIGEST_A, DIGEST_B]


def test_ignores_layers_of_other_media_types(tmp_path):
    name = parse_ollama_name("bge-m3")
    write_manifest(
        tmp_path,
        name,
        [
            model_layer(DIGEST_B, media_type="application/vnd.ollama.image.template"),
            model_layer(DIGEST_A),
        ],
    )

    assert local_model_digests(name, tmp_path) == [DIGEST_A]


def assert_error_names_lookup(excinfo, name: OllamaName, store_root: Path) -> None:
    """The message must say what was looked for and where (spec 0013)."""
    message = str(excinfo.value)
    assert name.model in message
    assert str(store_root) in message


def test_missing_store_root_raises_naming_model_and_path(tmp_path):
    name = parse_ollama_name("bge-m3")
    missing_root = tmp_path / "no-store-here"

    with pytest.raises(OllamaStoreError) as excinfo:
        local_model_digests(name, missing_root)
    assert_error_names_lookup(excinfo, name, missing_root)


def test_unknown_model_raises_naming_model_and_path(tmp_path):
    name = parse_ollama_name("bge-m3")
    (tmp_path / ollama_layout.MANIFESTS_DIRNAME).mkdir()

    with pytest.raises(OllamaStoreError) as excinfo:
        local_model_digests(name, tmp_path)
    assert_error_names_lookup(excinfo, name, tmp_path)


def test_malformed_manifest_json_raises_naming_model_and_path(tmp_path):
    name = parse_ollama_name("bge-m3")
    write_manifest(tmp_path, name, raw="{not json at all")

    with pytest.raises(OllamaStoreError) as excinfo:
        local_model_digests(name, tmp_path)
    assert_error_names_lookup(excinfo, name, tmp_path)


def test_manifest_without_model_layer_raises_naming_model_and_path(tmp_path):
    name = parse_ollama_name("bge-m3")
    write_manifest(
        tmp_path, name, [model_layer(DIGEST_A, media_type="application/vnd.ollama.image.params")]
    )

    with pytest.raises(OllamaStoreError) as excinfo:
        local_model_digests(name, tmp_path)
    assert_error_names_lookup(excinfo, name, tmp_path)


@pytest.mark.parametrize("digest", ["sha256:not-hex", "sha256:" + "a" * 40, "md5:" + "a" * 64])
def test_model_layer_digest_not_sha256_hex_raises(tmp_path, digest):
    name = parse_ollama_name("bge-m3")
    write_manifest(tmp_path, name, [{"mediaType": MODEL_MEDIA_TYPE, "digest": digest, "size": 100}])

    with pytest.raises(OllamaStoreError) as excinfo:
        local_model_digests(name, tmp_path)
    assert_error_names_lookup(excinfo, name, tmp_path)


def test_manifest_path_escaping_the_store_is_refused(tmp_path):
    # parse_ollama_name's alphabet gate keeps `..` out, so this belt
    # can only be reached by constructing the name directly — it must
    # stay: a refactor of the parse gate must not silently open a
    # read path outside the store (review round 2026-07-31).
    (tmp_path / ollama_layout.MANIFESTS_DIRNAME).mkdir()
    hostile = OllamaName(registry="..", namespace="..", model="..", tag="..")

    with pytest.raises(OllamaStoreError) as excinfo:
        local_model_digests(hostile, tmp_path)
    assert "escapes the store" in str(excinfo.value)


def test_deeply_nested_manifest_json_is_a_clean_error_not_a_crash(tmp_path):
    # A copied store is untrusted input: json.loads raises
    # RecursionError (not JSONDecodeError) on deep nesting — the
    # 0011/0012 traceback class (security round 2026-07-31).
    name = parse_ollama_name("bge-m3")
    write_manifest(tmp_path, name, raw="[" * 100_000 + "]" * 100_000)

    with pytest.raises(OllamaStoreError) as excinfo:
        local_model_digests(name, tmp_path)
    assert_error_names_lookup(excinfo, name, tmp_path)


# --- store-root resolution (fixed-order probe, adjudicated 2026-07-31) -----


def test_env_override_is_the_only_candidate(monkeypatch, tmp_path):
    # An explicit $OLLAMA_MODELS must never fall back elsewhere: an
    # error has to name the path the user pointed at.
    custom = tmp_path / "custom-store"
    monkeypatch.setenv(ollama_layout.MODELS_ENV_VAR, str(custom))

    assert ollama_layout.candidate_store_roots() == [custom]


def test_default_candidates_are_user_local_then_linux_system(monkeypatch):
    monkeypatch.delenv(ollama_layout.MODELS_ENV_VAR, raising=False)

    assert ollama_layout.candidate_store_roots() == [
        Path.home() / ".ollama" / "models",
        ollama_layout.LINUX_SYSTEM_STORE_ROOT,
    ]


def test_locate_returns_the_first_existing_candidate(monkeypatch, tmp_path):
    missing = tmp_path / "missing"
    existing = tmp_path / "existing"
    existing.mkdir()
    monkeypatch.setattr(
        "llm_preserver.ollama_store.candidate_store_roots", lambda: [missing, existing]
    )

    from llm_preserver.ollama_store import locate_store_root

    assert locate_store_root(parse_ollama_name("bge-m3")) == existing


def test_locate_error_names_the_model_and_every_path_checked(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setattr("llm_preserver.ollama_store.candidate_store_roots", lambda: [first, second])

    from llm_preserver.ollama_store import locate_store_root

    with pytest.raises(OllamaStoreError) as excinfo:
        locate_store_root(parse_ollama_name("bge-m3"))
    message = str(excinfo.value)
    assert "bge-m3" in message
    assert str(first) in message
    assert str(second) in message
