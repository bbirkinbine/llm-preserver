"""Shared archive/staging shapes for the spec 0019 cleanup tests.

Mirrors ``migrate_shapes.py``: plain helpers, no fixtures, so the
staging-cleanup modules build the same archive and the same residue
without repeating it. The residue mirrors the live 2026-08-12 trigger —
hf's ``.cache/huggingface/download/`` holding zero-byte ``.lock`` files
and ~124-byte ``.metadata`` sidecars, no payload and no
``*.incomplete``. Every archive lives in ``tmp_path``; never a real one.
"""

import errno
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import llm_preserver.pull as pull
from llm_preserver.archive import init_archive
from llm_preserver.pull_prepare import STAGING_DIRNAME

REPO_ID = "bartowski/tiny-chat-GGUF"
CREATOR, NAME = REPO_ID.split("/")
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
Q8_NAME = "tiny-chat-Q8_0.gguf"
INCLUDE_Q4 = ["*Q4_K_M*"]


def new_archive(tmp_path: Path) -> Path:
    """An initialized archive root under ``tmp_path``."""
    root = tmp_path / "archive"
    init_archive(root)
    return root


def model_dir(root: Path) -> Path:
    """Where the pulled repo lands (ADR 0003: one directory per repo)."""
    return root / "models" / CREATOR / NAME


def staging_leaf(root: Path) -> Path:
    """``.staging/<creator>/<model>/`` — the leaf a pull stages into."""
    return root / STAGING_DIRNAME / CREATOR / NAME


def creator_dir(root: Path) -> Path:
    """``.staging/<creator>/`` — the leaf's parent."""
    return root / STAGING_DIRNAME / CREATOR


def do_pull(root: Path, client: Any, **kwargs: Any) -> Path:
    """Pull the Q4 selection, auto-confirming whatever is asked."""
    kwargs.setdefault("include", INCLUDE_Q4)
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(root, REPO_ID, client, **kwargs)


def write_bookkeeping(leaf: Path, *shards: str) -> Path:
    """Write hf's local-dir bookkeeping into ``leaf`` and nothing else.

    The live residue shape: a zero-byte ``.lock`` and a small
    ``.metadata`` sidecar per shard under ``.cache/huggingface/download``.
    No payload and no ``*.incomplete`` — what a refused cleanup leaves
    behind, and what nothing in the tool removes on its own.
    """
    download = leaf / ".cache" / "huggingface" / "download"
    download.mkdir(parents=True, exist_ok=True)
    for shard in shards or (Q4_NAME,):
        (download / f"{shard}.lock").write_bytes(b"")
        (download / f"{shard}.metadata").write_bytes(b"e" * 124)
    return leaf


def failing_rmtree(target: Path, calls: list[Path] | None = None) -> Callable[..., Any]:
    """A ``shutil.rmtree`` that raises the live ENOTEMPTY on one path.

    macOS ``smbfs`` renames a still-open file to a hidden placeholder
    instead of unlinking it, so the parent ``rmdir`` still sees a
    non-empty directory — the confirmed 2026-08-12 failure, injected
    here as the real ``OSError(errno.ENOTEMPTY)`` it was. Every other
    path delegates to the real ``rmtree``, so nothing but the leaf
    under test changes behavior.

    Args:
        target: The path whose removal raises.
        calls: Optional list every attempted path is appended to, so a
            test can assert the removal was attempted at all.
    """
    real = shutil.rmtree

    def _rmtree(path: Any, *args: Any, **kwargs: Any) -> Any:
        if calls is not None:
            calls.append(Path(path))
        if Path(path) == target:
            raise OSError(errno.ENOTEMPTY, "Directory not empty", str(path))
        return real(path, *args, **kwargs)

    return _rmtree
