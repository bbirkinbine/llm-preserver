"""Ollama store-layout facts, shared by every Ollama-facing feature.

Single home for the on-disk layout constants so the views adapter
(spec 0002) and the match reader (spec 0013) cannot drift apart.
All values are external-authority facts (Ollama is MIT-licensed,
consulted not copied):

- Blob path is ``<store root>/blobs/sha256-<64 hex>``, digest = SHA256
  of the file bytes; manifests live at
  ``manifests/<registry>/<namespace>/<model>/<tag>``. Source:
  github.com/ollama/ollama ``manifest/paths.go``, ``manifest/layer.go``
  (fetched in-session 2026-07-30), plus a real store inspected on this
  machine. Watch: open PR #15735 ("manifest-v2") would move the
  manifest tree — re-verify these paths on Ollama upgrades.
- ``OLLAMA_MODELS`` overrides the store root; the default root is
  ``~/.ollama/models`` on macOS and Windows
  (``C:\\Users\\%username%\\.ollama\\models``) and
  ``/usr/share/ollama/.ollama/models`` on Linux standard installs.
  Source: https://docs.ollama.com/faq (fetched in-session 2026-07-31).
- A bare model name fills in registry ``registry.ollama.ai``,
  namespace ``library``, tag ``latest``. Source: github.com/ollama/
  ollama ``types/model/name.go`` (``defaultHost`` /
  ``defaultNamespace`` / ``defaultTag``; fetched in-session
  2026-07-31).
"""

import os
from pathlib import Path

BLOBS_DIRNAME = "blobs"
BLOB_PREFIX = "sha256-"
MANIFESTS_DIRNAME = "manifests"
REGISTRY_DIRNAME = "registry.ollama.ai"
DEFAULT_NAMESPACE = "library"
DEFAULT_TAG = "latest"

# The manifest layer that carries the GGUF weights themselves (other
# layers hold template/params/projector data). Source: github.com/
# ollama/ollama ``manifest/layer.go`` media types (fetched in-session
# 2026-07-30), confirmed against a real store on this machine.
MODEL_LAYER_MEDIA_TYPE = "application/vnd.ollama.image.model"

MODELS_ENV_VAR = "OLLAMA_MODELS"

# The Linux install script runs Ollama as a systemd service under a
# dedicated `ollama` user whose home is /usr/share/ollama, so that is
# where a stock Linux install keeps its store. Source:
# https://docs.ollama.com/faq (fetched in-session 2026-07-31).
LINUX_SYSTEM_STORE_ROOT = Path("/usr/share/ollama/.ollama/models")


def default_store_root() -> Path:
    """The user-local store root: macOS, Windows, and manual Linux runs."""
    return Path.home() / ".ollama" / "models"


def candidate_store_roots() -> list[Path]:
    """Store roots to probe, in fixed documented order.

    ``$OLLAMA_MODELS`` set is an explicit override — it is the only
    candidate, never a fallback chain (an error must name the path the
    user pointed at, not a silently substituted one). Otherwise the
    user-local root, then the Linux system-install root (adjudicated
    2026-07-31: a stock Linux service install must work out of the
    box). Callers take the first that exists and disclose which.
    """
    override = os.environ.get(MODELS_ENV_VAR)
    if override:
        return [Path(override)]
    return [default_store_root(), LINUX_SYSTEM_STORE_ROOT]
