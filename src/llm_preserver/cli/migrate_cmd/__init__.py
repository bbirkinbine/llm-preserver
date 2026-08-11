"""The migrate command package (spec 0017, ADR 0003).

Importing this package registers the command on the Typer app.
"""

from llm_preserver.cli.migrate_cmd.command import migrate

__all__ = ["migrate"]
