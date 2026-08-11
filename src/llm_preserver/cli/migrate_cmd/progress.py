"""Live TTY progress while a migration runs (spec 0017).

Live-use finding, 2026-08-11: after answering the confirmation on a
multi-terabyte archive, nothing printed until the run finished. Even
when the work is seconds, silence during a bulk operation on someone's
only copy of 5 TB reads as a hang — and in ``--to`` mode a single file
copy really can run for ten minutes.

The same shape spec 0009 settled on for long verify hashes: render only
to a terminal, so cron and piped runs stay byte-identical to a
progress-free run, and keep it on stderr so a redirected stdout still
captures exactly the report.
"""

import sys
from typing import TextIO

from llm_preserver.pull_preflight import human_size
from llm_preserver.render import clean_text


class MigrateProgress:
    """Per-directory and per-file status lines on stderr.

    Attributes are deliberately few: this prints, it does not decide.
    Whether a human is watching is answered once, at construction, by
    the stream itself.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._enabled = self._stream.isatty()

    def on_directory_start(self, model_id: str, file_count: int, total_bytes: int) -> None:
        """One line as each directory begins converting."""
        if not self._enabled:
            return
        noun = "file" if file_count == 1 else "files"
        line = f"converting {model_id} ({file_count} {noun}, {human_size(total_bytes)})"
        self._write(clean_text(line, single_line=True))

    def on_file(self, name: str, size: int) -> None:
        """One indented line per file, before it moves or copies.

        ``size`` is 0 for an in-place rename — the operation is
        metadata-only, so naming a byte count there would imply a
        transfer that is not happening.
        """
        if not self._enabled:
            return
        suffix = f"  {human_size(size)}" if size else ""
        self._write(clean_text(f"  {name}{suffix}", single_line=True))

    def _write(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()
