"""The verify command package (specs 0009, 0012, 0017).

Split from a single module when spec 0017 pushed it past the 300-line
cap. Importing this package registers the command on the Typer app, and
re-exports the names the previous module exposed so no import site
changes.
"""

from llm_preserver.cli.verify_cmd.command import verify
from llm_preserver.cli.verify_cmd.progress import ProgressRenderer

__all__ = ["ProgressRenderer", "verify"]
