"""Hub access seam: the only package that imports ``huggingface_hub``.

Split from a single ``hub.py`` when spec 0006's discovery methods
crossed the 300-line cap: ``errors`` (fault domains + mapping),
``types`` (data types + the client protocol), ``client`` (the real
Hugging Face implementation). This ``__init__`` re-exports the
public API so every existing ``llm_preserver.hub`` import keeps
working.
"""

from .client import HubClient
from .errors import (
    PullEnvError,
    PullError,
    PullHubError,
    PullIntegrityError,
    PullUserError,
    map_hub_exception,
)
from .types import HubClientProtocol, RepoFile, RepoInfo

__all__ = [
    "HubClient",
    "HubClientProtocol",
    "PullEnvError",
    "PullError",
    "PullHubError",
    "PullIntegrityError",
    "PullUserError",
    "RepoFile",
    "RepoInfo",
    "map_hub_exception",
]
