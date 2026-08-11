"""Live progress during a migration — spec 0017, live use 2026-08-11.

Brian, after confirming an in-place run on a multi-terabyte archive:
"nothing else showed". Even when the work is seconds, silence during a
bulk operation on someone's only copy reads as a hang — and in ``--to``
mode a single file copy really can take ten minutes.

Spec 0009 settled the shape for long verify hashes and it applies
unchanged here: render only to a terminal, so cron and piped runs stay
byte-identical to a progress-free run.
"""

import io
from pathlib import Path

from llm_preserver.cli.migrate_cmd.progress import MigrateProgress


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def test_a_terminal_gets_a_line_per_directory() -> None:
    stream = FakeTTY()

    MigrateProgress(stream).on_directory_start("Qwen/tiny-chat", 3, 1024)

    out = stream.getvalue()
    assert "Qwen/tiny-chat" in out
    assert "3 files" in out


def test_a_terminal_gets_a_line_per_file() -> None:
    # The unit that actually takes time in --to mode.
    stream = FakeTTY()

    MigrateProgress(stream).on_file("model-Q4_K_M.gguf", 16 * 1024**3)

    assert "model-Q4_K_M.gguf" in stream.getvalue()


def test_a_rename_names_no_byte_count() -> None:
    # An in-place move is metadata-only; printing a size there would
    # imply a transfer that is not happening.
    stream = FakeTTY()

    MigrateProgress(stream).on_file("model-Q4_K_M.gguf", 0)

    assert "GiB" not in stream.getvalue()
    assert "model-Q4_K_M.gguf" in stream.getvalue()


def test_a_pipe_gets_nothing() -> None:
    # The spec 0009 contract: a scripted run's output stays
    # byte-identical to a progress-free one.
    stream = io.StringIO()  # isatty() is False

    progress = MigrateProgress(stream)
    progress.on_directory_start("Qwen/tiny-chat", 3, 1024)
    progress.on_file("model-Q4_K_M.gguf", 999)

    assert stream.getvalue() == ""


def test_progress_text_is_scrubbed(tmp_path: Path) -> None:
    # Model ids reach this line, and a record is untrusted bytes: no
    # control character may forge a line of tool output (0007 class).
    stream = FakeTTY()

    MigrateProgress(stream).on_directory_start("evil/\r\nmigrated 99 directories", 1, 1)

    assert stream.getvalue().count("\n") == 1
