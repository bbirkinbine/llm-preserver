"""Shared shapes for the spec 0026 pass 4 wiring tests (pull + meter).

Plain helpers, no fixtures, like ``staging_shapes.py``. The meter is
never mocked: ``RecordingProgress`` is a real ``PullProgress`` that
also logs every call it receives, in order, so a test can pin both the
arguments the transfer loop passes and the state the meter ends in.
Every archive lives in ``tmp_path``; never a real one.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from staging_shapes import REPO_ID, model_dir, staging_leaf

import llm_preserver.pull as pull
from llm_preserver.pull_progress import PullProgress

__all__ = [
    "COMMIT",
    "HUB_FILES",
    "Q4_BYTES",
    "Q4_NAME",
    "Q8_BYTES",
    "Q8_NAME",
    "README_BYTES",
    "README_NAME",
    "REPO_ID",
    "RecordingProgress",
    "archive_tree",
    "do_pull",
    "model_dir",
    "staging_leaf",
]

COMMIT = "a" * 40  # FakeHubClient's default resolved commit
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
Q8_NAME = "tiny-chat-Q8_0.gguf"
Q8_BYTES = b"q8 weight bytes"
README_NAME = "README.md"
README_BYTES = b"# tiny-chat quantized\n"
HUB_FILES = [
    (Q4_NAME, Q4_BYTES, True),
    (Q8_NAME, Q8_BYTES, True),
    (README_NAME, README_BYTES, False),
]


class RecordingProgress(PullProgress):
    """A real meter that also records each call, in order, in ``events``.

    Events are plain tuples so a whole sequence compares with ``==``:
    ``("begin", files, adopted_bytes)``, ``("start_file", index, path,
    declared_size, already_staged, cached_locally)``, ``("transfer",
    n)``, ``("start_hash", total)``, ``("hash", n, phase)`` (the phase
    the meter reports right after counting), ``("finish_file", n)``.
    Tests append their own markers (e.g. ``("on_transfer_start",)``) to
    place a seam in the same timeline.
    """

    def __init__(self) -> None:
        super().__init__(clock=lambda: 0.0)
        self.events: list[tuple[Any, ...]] = []  # Any: heterogeneous call args

    def begin(self, files: Sequence[tuple[str, int | None]], *, adopted_bytes: int = 0) -> None:
        self.events.append(("begin", [tuple(f) for f in files], adopted_bytes))
        super().begin(files, adopted_bytes=adopted_bytes)

    def start_file(
        self,
        index: int,
        path: str,
        *,
        declared_size: int | None,
        already_staged: bool,
        cached_locally: bool = False,
    ) -> None:
        self.events.append(
            ("start_file", index, path, declared_size, already_staged, cached_locally)
        )
        super().start_file(
            index,
            path,
            declared_size=declared_size,
            already_staged=already_staged,
            cached_locally=cached_locally,
        )

    def add_transfer_bytes(self, n: object) -> None:
        self.events.append(("transfer", n))
        super().add_transfer_bytes(n)

    def start_hash(self, total: int | None) -> None:
        self.events.append(("start_hash", total))
        super().start_hash(total)

    def add_hash_bytes(self, n: object) -> None:
        super().add_hash_bytes(n)
        self.events.append(("hash", n, self.snapshot().phase))

    def finish_file(self, archived_bytes: int) -> None:
        self.events.append(("finish_file", archived_bytes))
        super().finish_file(archived_bytes)

    def names(self) -> list[str]:
        """The event names alone, for ordering asserts."""
        return [event[0] for event in self.events]


def do_pull(root: Path, client: Any, **kwargs: Any) -> Path:
    """Pull the Q4 selection (plus docs), auto-confirming every prompt.

    ``client`` is the conftest ``FakeHubClient`` (Any: duck-typed seam).
    """
    kwargs.setdefault("include", [f"*{Q4_NAME}"])
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(root, REPO_ID, client, **kwargs)


def archive_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under ``models/`` as rel path -> (bytes, permission bits)."""
    models = root / "models"
    return {
        str(path.relative_to(models)): (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in sorted(models.rglob("*"))
        if path.is_file()
    }
