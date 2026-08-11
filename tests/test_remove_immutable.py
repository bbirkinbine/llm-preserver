"""``remove`` deletes locked payload — found 2026-08-11, pre-existing.

ADR 0001 locks payload after download; over SMB that lock is the BSD
immutable flag, and ``unlink(2)`` on an immutable file fails with
EPERM. ``remove`` deletes the record *first* on purpose (crash-safe
order, spec 0010), so on a real NAS it would have destroyed a model's
source of truth and then failed on its first weight — leaving hundreds
of gigabytes orphaned with nothing describing them.

Found while fixing the same bug in ``migrate``; the two now share
``file_locks``. The nastiness is the ordering, so that is what the
first test pins.
"""

import os
import stat
from pathlib import Path

import pytest

from llm_preserver.remove import execute_removal, plan_removal

pytestmark = pytest.mark.skipif(
    not hasattr(os, "chflags"), reason="BSD file flags are macOS/BSD only"
)

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"weight bytes"


@pytest.fixture
def locked_model(tmp_path: Path, write_model, sample_record_dict) -> tuple[Path, Path]:
    """An archive whose payload carries the immutable flag."""
    from llm_preserver.archive import init_archive

    archive = tmp_path / "archive"
    init_archive(archive)
    record = sample_record_dict(hub_id="acme/tiny-chat")
    record["artifacts"][0]["files"] = [
        {"path": PAYLOAD_REL, "sha256": "0" * 64, "size": len(PAYLOAD), "source": "original"}
    ]
    model_dir = write_model(archive, record)
    target = model_dir / PAYLOAD_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PAYLOAD)
    os.chflags(target, target.stat().st_flags | stat.UF_IMMUTABLE)
    yield archive, model_dir
    for path in archive.rglob("*"):
        if path.is_file():
            with __import__("contextlib").suppress(OSError):
                os.chflags(path, path.stat().st_flags & ~stat.UF_IMMUTABLE)


def test_remove_deletes_payload_that_carries_the_immutable_flag(locked_model) -> None:
    archive, model_dir = locked_model

    execute_removal(archive, plan_removal(archive, "acme/tiny-chat", None))

    assert not model_dir.exists()


def test_a_failed_remove_never_strands_the_record(locked_model) -> None:
    # The ordering is what made this dangerous: the record goes first,
    # so a payload failure would leave the bytes with nothing that
    # describes them. Nothing may survive under models/ — the archive
    # marker itself is not payload and stays.
    archive, _ = locked_model

    execute_removal(archive, plan_removal(archive, "acme/tiny-chat", None))

    orphans = [p for p in (archive / "models").rglob("*") if p.is_file()]
    assert orphans == [], f"payload survived without a record: {orphans}"
