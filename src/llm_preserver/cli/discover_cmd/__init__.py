"""The discover command package: deterministic name-to-pull navigation.

Split from a single ``discover_cmd.py`` at the 300-line rule when
spec 0013 added match mode. ``command`` holds the Typer surface and
mode dispatch; ``flow`` holds the interactive search/tree stages.
Importing the package registers the command (via ``command``).
"""

from .command import discover

__all__ = ["discover"]
