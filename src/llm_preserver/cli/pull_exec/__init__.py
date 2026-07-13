"""Shared pull-execution core for the pull and discover commands.

Split into a package when the resume-command hint (spec 0007) pushed
the module past the file-size cap: ``plumbing`` (client seam, logging,
fault-domain exits), ``prompts`` (confirmations, file picking), and
``flow`` (``run_pull``). The public surface is unchanged — import from
``llm_preserver.cli.pull_exec`` as before.
"""

from .flow import run_pull
from .plumbing import (
    PULL_FAULT_DOMAINS,
    exit_for_pull_error,
    log_underlying_failure,
    make_hub_client,
    setup_logging,
)
from .prompts import confirm_or_stop, prompt_for_selection

__all__ = [
    "PULL_FAULT_DOMAINS",
    "confirm_or_stop",
    "exit_for_pull_error",
    "log_underlying_failure",
    "make_hub_client",
    "prompt_for_selection",
    "run_pull",
    "setup_logging",
]
