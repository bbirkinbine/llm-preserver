"""Read-only reader for the local Ollama store (spec 0013).

Resolves an Ollama model name to its local manifest and extracts the
model-layer SHA256 digests — the byte-identity anchor the matcher
compares against hub-declared file hashes. Never writes anything and
needs no running Ollama server; layout facts come from
:mod:`llm_preserver.ollama_layout` (provenance pinned there).

The manifest is untrusted local input (a store may have been copied
from elsewhere): JSON is parsed defensively, digests are validated as
``sha256:<64 hex>`` before use, and the composed manifest path must
resolve inside the store root — every failure is an
:class:`OllamaStoreError` naming what was looked for and where.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from llm_preserver.hub_discovery import looks_like_repo_id
from llm_preserver.ollama_layout import (
    DEFAULT_NAMESPACE,
    DEFAULT_TAG,
    MANIFESTS_DIRNAME,
    MODEL_LAYER_MEDIA_TYPE,
    REGISTRY_DIRNAME,
    candidate_store_roots,
)

_DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})\Z")

# Ollama's own name alphabet, first character alphanumeric (matching
# ID_COMPONENT_RE's posture — a leading dash would put a flag-shaped
# token into pasteable output, the 0007 class). Enforced at parse
# time, so every name component downstream — manifest path segments,
# hint text — is provably inert (validated, never merely quoted).
_SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

_HF_REGISTRY_PREFIX = "hf.co/"


class OllamaStoreError(Exception):
    """A local-store lookup failure; the message names what and where."""


@dataclass(frozen=True)
class OllamaName:
    """An Ollama model name normalized to its manifest-path components."""

    registry: str
    namespace: str
    model: str
    tag: str

    def display(self) -> str:
        """The name as a user would type it, defaults omitted."""
        parts = []
        if self.registry != REGISTRY_DIRNAME:
            parts.append(self.registry)
        if parts or self.namespace != DEFAULT_NAMESPACE:
            parts.append(self.namespace)
        parts.append(self.model)
        return f"{'/'.join(parts)}:{self.tag}"


def parse_ollama_name(raw: str) -> OllamaName:
    """Parse ``[registry/][namespace/]model[:tag]``, filling Ollama's defaults.

    Args:
        raw: The name as typed (e.g. ``bge-m3``, ``bge-m3:q8_0``,
            ``hf.co/org/repo:Q4_K_M``).

    Returns:
        The four manifest-path components, defaults filled in.

    Raises:
        OllamaStoreError: If the name is empty, carries more than three
            ``/``-separated segments, or any component falls outside
            Ollama's name alphabet (``[A-Za-z0-9._-]``, alphanumeric
            first) — which also keeps ``..``, ``/``, and flag-shaped
            tokens out of manifest paths and hint text.
    """
    base, colon, tag = raw.partition(":")
    if not colon:
        tag = DEFAULT_TAG
    segments = base.split("/")
    if (
        not base
        or len(segments) > 3
        or not all(_SAFE_COMPONENT_RE.fullmatch(part) for part in [*segments, tag])
    ):
        raise OllamaStoreError(
            f"not a valid Ollama model name: {raw!r} — expected [registry/][namespace/]model[:tag]"
        )
    if len(segments) == 1:
        registry, namespace, model = REGISTRY_DIRNAME, DEFAULT_NAMESPACE, segments[0]
    elif len(segments) == 2:
        registry, namespace, model = REGISTRY_DIRNAME, segments[0], segments[1]
    else:
        registry, namespace, model = segments
    return OllamaName(registry=registry, namespace=namespace, model=model, tag=tag)


def ollama_shape_hint(raw_id: str) -> str | None:
    """A recovery hint when a rejected repo id is Ollama-shaped (spec 0013).

    Detection only, on an id the hub's validator already refused: the
    id is never rewritten, and every token echoed into a pasteable
    command is validated against Ollama's name alphabet first — a
    component outside it means no hint at all.

    Args:
        raw_id: The repo id exactly as the user typed it.

    Returns:
        A hint naming the deterministic recovery command, or None when
        the id is not Ollama-shaped (or unsafe to echo). Never raises.
    """
    base, colon, tag = raw_id.partition(":")
    if base.startswith(_HF_REGISTRY_PREFIX):
        repo_id = base.removeprefix(_HF_REGISTRY_PREFIX)
        if "/" not in repo_id or not looks_like_repo_id(repo_id):
            return None
        translation = (
            "hint: this is Ollama's hf.co pull syntax — the direct equivalent is: "
            f"llm-preserver pull {repo_id}"
        )
        if not colon:
            return translation
        if not _SAFE_COMPONENT_RE.fullmatch(tag):
            return None
        return f"{translation} --include '*{tag}*' (the tag names the quantization)"
    if not colon:
        return None
    try:
        # parse_ollama_name enforces the safe alphabet on every
        # component, so a parsed name is provably inert in hint text.
        name = parse_ollama_name(raw_id)
    except OllamaStoreError:
        return None
    return (
        f"hint: '{raw_id}' looks like an Ollama model name, which has no direct "
        f"Hugging Face equivalent — search the hub by name: llm-preserver discover "
        f"{name.model} — or map the local model to hub repos by digest: "
        f"llm-preserver discover --match-ollama {name.display()}"
    )


def locate_store_root(name: OllamaName) -> Path:
    """The first existing store root from the documented candidates.

    Probe order (fixed, disclosed by the caller): ``$OLLAMA_MODELS``
    when set — an explicit override, never a fallback chain — else the
    user-local root, else the Linux system-install root.

    Raises:
        OllamaStoreError: When no candidate exists, naming the model
            and every path checked.
    """
    candidates = candidate_store_roots()
    for root in candidates:
        if root.is_dir():
            return root
    checked = ", ".join(str(root) for root in candidates)
    raise OllamaStoreError(
        f"no Ollama store found while looking for {name.display()} — checked: {checked} "
        "— set $OLLAMA_MODELS if the store lives elsewhere"
    )


def local_model_digests(name: OllamaName, store_root: Path) -> list[str]:
    """Read the model-layer digests for ``name`` from the local store.

    Args:
        name: The parsed model name.
        store_root: The Ollama store root (``$OLLAMA_MODELS`` or the
            default; resolution is the caller's job).

    Returns:
        The bare 64-hex SHA256 of every ``vnd.ollama.image.model``
        layer in the manifest, in manifest order.

    Raises:
        OllamaStoreError: If the store or manifest is missing, the
            manifest path escapes the store root, the manifest is
            unreadable or malformed, or a model-layer digest is not
            ``sha256:<64 hex>``.
    """
    looked = f"{name.display()} in the Ollama store at {store_root}"
    if not store_root.is_dir():
        raise OllamaStoreError(
            f"no Ollama store at {store_root} while looking for {name.display()} "
            "— set $OLLAMA_MODELS if the store lives elsewhere"
        )
    manifest_path = (
        store_root / MANIFESTS_DIRNAME / name.registry / name.namespace / name.model / name.tag
    )
    # Name components are user-typed: refuse a path that resolves out
    # of the store (`..` or a hostile symlink) rather than read it.
    if not manifest_path.resolve().is_relative_to(store_root.resolve()):
        raise OllamaStoreError(f"manifest path escapes the store while looking for {looked}")
    if not manifest_path.is_file():
        raise OllamaStoreError(f"no manifest for {looked} (expected {manifest_path})")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        # ValueError subsumes JSONDecodeError; RecursionError covers a
        # hostile deeply-nested manifest (the store may have been
        # copied from anywhere) — a crash here is the 0011/0012 class.
        raise OllamaStoreError(f"could not read the manifest for {looked}: {exc}") from exc
    layers = manifest.get("layers") if isinstance(manifest, dict) else None
    digests: list[str] = []
    for layer in layers if isinstance(layers, list) else []:
        if not isinstance(layer, dict) or layer.get("mediaType") != MODEL_LAYER_MEDIA_TYPE:
            continue
        digest = layer.get("digest")
        matched = _DIGEST_RE.fullmatch(digest) if isinstance(digest, str) else None
        if matched is None:
            raise OllamaStoreError(
                f"model layer of {looked} has digest {digest!r}, not sha256:<64 hex>"
            )
        digests.append(matched.group(1))
    if not digests:
        raise OllamaStoreError(f"manifest for {looked} has no model layer to match against")
    return digests
