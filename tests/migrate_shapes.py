"""Archive shapes for the spec 0017 pass 2 (``migrate``) suite.

Plain builder functions rather than fixtures: every migrate test needs a
record whose artifacts carry *different* ``source_repo`` values, and the
shared ``sample_record_dict`` fixture models one artifact from one
source — the post-ADR-0003 shape. Reusing it here would mean overwriting
the very field under test in every test body.

Two shapes are reproduced, both measured on the live archive
(spec 0017 criterion 9 — 3 pure renames, 8 splits):

* **pure rename** — every artifact foreign and sharing one source repo:
  ``Qwen/tiny-chat`` holds only ``unsloth/tiny-chat-GGUF``'s files, so
  the whole directory becomes that repo's directory and the name it
  carried belongs to a model the archive never held (ADR 0003 fact 2).
* **split** — the directory's own snapshot plus a foreign quant:
  ``acme/tiny-chat`` holds its own ``hf-snapshot/`` and
  ``other/tiny-chat-GGUF``'s ``gguf/``, so only the quant moves out.

A third shape, ``two_publisher_archive``, puts two source repos' files
in one ``gguf/`` directory — legal since ``update_record`` keys
artifacts by ``(format, source_repo)`` — and is the shape that proves a
migration moves *files*, not subtrees.
"""

import contextlib
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import click

from llm_preserver.archive import init_archive
from llm_preserver.layout import source_repo_url
from llm_preserver.records import RECORD_FILENAME

FULL_COMMIT_HASH = "a" * 40

Q4_REL = "gguf/tiny-chat-Q4_K_M.gguf"
Q4 = b"q4 weight bytes"
Q8_REL = "gguf/tiny-chat-Q8_0.gguf"
Q8 = b"q8 weight bytes"
SNAPSHOT_REL = "hf-snapshot/model.safetensors"
SNAPSHOT = b"safetensors weight bytes"
CONFIG_REL = "hf-snapshot/config.json"
CONFIG = b'{"model_type": "tiny"}\n'

RENAME_DIR_ID = "Qwen/tiny-chat"
"""Directory named for a model whose weights the archive does not hold."""
RENAME_TARGET_ID = "unsloth/tiny-chat-GGUF"
"""The repo that actually published every file in that directory."""

SPLIT_DIR_ID = "acme/tiny-chat"
SPLIT_TARGET_ID = "other/tiny-chat-GGUF"


def sha256_hex(content: bytes) -> str:
    """SHA256 hex digest of ``content``."""
    return hashlib.sha256(content).hexdigest()


def entry_for(rel_path: str, content: bytes) -> dict[str, object]:
    """FileEntry dict whose sha256 and size match ``content``."""
    return {
        "path": rel_path,
        "sha256": sha256_hex(content),
        "size": len(content),
        "source": "original",
    }


def artifact_for(
    fmt: str, source_repo_id: str | None, payloads: Mapping[str, bytes]
) -> dict[str, object]:
    """ArtifactEntry dict for one publisher's files in one format.

    ``source_repo_id`` None writes ``source_repo: null`` — the nullable
    case spec 0017 open question 3 covers.
    """
    return {
        "format": fmt,
        "quantization": None,
        "source_repo": None if source_repo_id is None else source_repo_url(source_repo_id),
        "revision": FULL_COMMIT_HASH,
        "download_date": "2026-07-09",
        "runtime_tested": None,
        "provenance": "hashed-locally",
        "files": [entry_for(rel, content) for rel, content in payloads.items()],
    }


def record_dict(model_id: str, artifacts: Sequence[dict[str, object]]) -> dict[str, object]:
    """A valid record for ``model_id`` carrying the given artifacts."""
    return {
        "name": model_id.partition("/")[2],
        "hub_id": model_id,
        "roles": ["chat"],
        "license": "apache-2.0",
        "parameter_count": "1B",
        "context_length": 4096,
        "notes": None,
        "artifacts": list(artifacts),
    }


def build_directory(
    archive_root: Path,
    model_id: str,
    artifacts: Sequence[tuple[str, str | None, Mapping[str, bytes]]],
) -> Path:
    """Create ``models/<owner>/<repo>`` with a record and real payload.

    Payload bytes are written for real (unlike ``write_model``): these
    tests move files and re-verify hashes, so the disk contents are the
    subject, not a detail.

    Args:
        archive_root: The archive root.
        model_id: The directory's ``<owner>/<repo>``.
        artifacts: ``(format, source_repo_id, {rel_path: bytes})``
            triples, one per publisher-and-format.

    Returns:
        The model directory.
    """
    owner, _, repo = model_id.partition("/")
    model_dir = archive_root / "models" / owner / repo
    model_dir.mkdir(parents=True, exist_ok=True)
    entries = [artifact_for(fmt, source, payloads) for fmt, source, payloads in artifacts]
    (model_dir / RECORD_FILENAME).write_text(
        json.dumps(record_dict(model_id, entries), indent=2) + "\n", encoding="utf-8"
    )
    for _fmt, _source, payloads in artifacts:
        for rel, content in payloads.items():
            target = model_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return model_dir


def init_archive_dir(base: Path) -> Path:
    """An initialized, empty archive at ``base/archive``."""
    root = base / "archive"
    init_archive(root)
    return root


def pure_rename_archive(archive_root: Path) -> Path:
    """``Qwen/tiny-chat`` holding only ``unsloth/tiny-chat-GGUF``'s files."""
    return build_directory(
        archive_root,
        RENAME_DIR_ID,
        [("gguf", RENAME_TARGET_ID, {Q4_REL: Q4, Q8_REL: Q8})],
    )


def split_archive(archive_root: Path) -> Path:
    """``acme/tiny-chat``: its own snapshot plus a foreign quant."""
    return build_directory(
        archive_root,
        SPLIT_DIR_ID,
        [
            ("hf-snapshot", SPLIT_DIR_ID, {SNAPSHOT_REL: SNAPSHOT, CONFIG_REL: CONFIG}),
            ("gguf", SPLIT_TARGET_ID, {Q4_REL: Q4}),
        ],
    )


def two_publisher_archive(archive_root: Path) -> Path:
    """One ``gguf/`` directory holding two source repos' files.

    Legal on the live archive: ``update_record`` keys artifacts by
    ``(format, source_repo)``, and ``require_single_snapshot_source``
    only ever guarded ``--whole-repo``. Moving the subtree would
    relocate the directory's own bytes along with the foreign ones.
    """
    return build_directory(
        archive_root,
        SPLIT_DIR_ID,
        [
            ("gguf", SPLIT_DIR_ID, {Q4_REL: Q4}),
            ("gguf", SPLIT_TARGET_ID, {Q8_REL: Q8}),
        ],
    )


def tree_snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under ``root``: file bytes, or None for a directory.

    Directories are included so that a removed (or newly created) empty
    directory shows up as a difference — ``migrate``'s ``rmdir`` step is
    exactly that kind of change.
    """
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[rel] = b"symlink:" + str(path.readlink()).encode("utf-8")
        elif path.is_dir():
            snapshot[rel] = None
        else:
            snapshot[rel] = path.read_bytes()
    return snapshot


def combined_output(result: object) -> str:
    """stdout plus stderr when captured separately (click version dependent).

    Substring asserts only — never count lines through this helper: this
    click version already folds stderr into ``result.output``, so every
    stderr line appears twice (``.claude/rules/python-code.md``).
    """
    out = result.output  # type: ignore[attr-defined]
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr  # type: ignore[attr-defined]
    return out


def output_of(result: object) -> str:
    """Unstyled combined output, safe for substring asserts."""
    return click.unstyle(combined_output(result))


def stdout_of(result: object) -> str:
    """Unstyled stdout alone — the stream that is safe to count lines on."""
    return click.unstyle(result.stdout)  # type: ignore[attr-defined]
