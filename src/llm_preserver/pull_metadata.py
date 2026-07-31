"""The two sanctioned extra hub metadata calls a pull may make.

Spec 0003 mandates one metadata call per pull; these are the two
adjudicated exceptions, both advisory inputs that must never abort a
pull: the root-level ``adapter_config.json`` fetch (spec 0005) and the
base-model rename resolution (spec 0006). Split out of
``pull_prepare.py`` (300-line rule).
"""

import json
import logging
import tempfile
from pathlib import Path

from llm_preserver.hub import HubClientProtocol, PullError, RepoInfo
from llm_preserver.hub_discovery import looks_like_repo_id

logger = logging.getLogger(__name__)

# An adapter config is kilobytes (peft serializes a flat dict); a hub
# file claiming to be one at megabyte scale is not worth fetching for
# an advisory.
_ADAPTER_CONFIG_MAX_BYTES = 1024 * 1024


def fetch_adapter_base(
    client: HubClientProtocol, repo_id: str, info: RepoInfo
) -> tuple[str | None, bool]:
    """Read ``base_model_name_or_path`` from the repo's adapter config.

    Returns:
        ``(base_model_pointer, fetched)`` — the pointer is None for
        any unusable config; ``fetched`` is True whenever a download
        was attempted, so the plan report can disclose it.

    Provenance: peft ``src/peft/config.py`` — ``save_pretrained``
    writes ``adapter_config.json`` at the repo root with the
    ``base_model_name_or_path`` field
    (https://github.com/huggingface/peft, Apache-2.0, retrieved
    2026-07-12).

    Adjudicated 2026-07-12: accuracy beats purity — when the tree
    ships a root-level ``adapter_config.json``, both real pulls and
    ``--plan`` fetch that small file (into a throwaway temp dir,
    never the archive or its staging) so the adapter-base advisory
    can name the exact follow-up pull, and say so out loud. Hub data
    is untrusted, and an advisory input must never abort a pull:
    oversized, malformed, unfetchable, or non-object configs all
    yield None. Root-only matching also keeps a nested decoy from
    shadowing the real config.
    """
    config = next((f for f in info.files if f.path == "adapter_config.json"), None)
    if config is None:
        return None, False
    if config.size is None or config.size > _ADAPTER_CONFIG_MAX_BYTES:
        # An undeclared size is untrusted, not unlimited: this fetch
        # can run with zero prompts, so it only fires when the hub
        # declares a size the cap can vouch for.
        logger.debug("adapter_config.json declares size %s: not fetching", config.size)
        return None, False
    logger.info("fetching %s to read its base-model pointer (advisory only)", config.path)
    try:
        with tempfile.TemporaryDirectory(prefix="llm-preserver-advisory-") as scratch:
            local = client.download(
                repo_id=repo_id,
                filename=config.path,
                revision=info.commit,
                dest_dir=Path(scratch),
            )
            parsed = json.loads(local.read_text(encoding="utf-8"))
    except (OSError, ValueError, PullError) as exc:
        logger.debug("adapter_config.json unusable for the advisory: %s", exc)
        return None, True
    if not isinstance(parsed, dict):
        return None, True
    base = parsed.get("base_model_name_or_path")
    return (base if isinstance(base, str) and base else None), True


def resolved_base_model(client: HubClientProtocol, base_model: str | None) -> str | None:
    """Resolve a declared base model to its current hub id.

    Card metadata goes stale when a parent repo is renamed (the hub
    redirects the old name); recording or proposing a dead name in a
    preservation tool ages badly. One light metadata call resolves it
    (adjudicated 2026-07-13 — the second sanctioned exception to the
    one-metadata-call rule, same rationale as the adapter-config
    fetch: accuracy beats purity, disclosed out loud). Any failure
    falls back to the declared name: base resolution is advisory
    input and must never abort a pull.
    """
    if not base_model or not looks_like_repo_id(base_model):
        return base_model
    try:
        summary = client.model_summary(base_model)
    except PullError:
        return base_model
    if summary.repo_id != base_model:
        logger.info(
            "declared base model %s was renamed on the hub — using its current id %s",
            base_model,
            summary.repo_id,
        )
    return summary.repo_id
