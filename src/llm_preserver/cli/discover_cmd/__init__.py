"""The discover command package: deterministic name-to-pull navigation.

Split from a single ``discover_cmd.py`` at the 300-line rule when
spec 0013 added match mode, and again when spec 0015's windowed paging
grew the stages. ``command`` holds the Typer surface and mode
dispatch; ``flow`` orchestrates search → tree → pull; ``stages`` holds
the two windowed listing stages; ``prompts`` their frame and prompt
plumbing. Frame sizing lives in ``cli.window``, one package up, since
spec 0018 gave ``pull``'s file listing the same treatment. Importing
the package registers the command (via ``command``).
"""

from .command import discover

__all__ = ["discover"]
