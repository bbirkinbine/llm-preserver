"""Typer CLI package: init, status, show, pull.

Split from a single ``cli.py`` at the 300-line rule. The public API is
unchanged: ``app`` (the Typer application, also the console-script
entry point) and ``HubClient`` (the seam tests monkeypatch to inject a
fake hub; the pull command resolves it through this module at call
time).
"""

from llm_preserver.hub import HubClient

from . import archive_cmds, pull_cmd  # noqa: F401  (importing registers the commands)
from .app import app

__all__ = ["HubClient", "app"]
