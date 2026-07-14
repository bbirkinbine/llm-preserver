"""Tests for verify's live progress (spec 0009, live-use adjudication).

A TTY gets a ``checking`` line per model and an in-place byte counter
per hashed file; a non-TTY (cron, pipes, CliRunner) gets no progress
output at all, so the report stays byte-identical to a progress-free
run. The renderer is tested directly with a fake TTY stream and a fake
clock; the core's event firing is tested through ``ProgressEvents``.
"""

import hashlib
import io
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.archive import init_archive
from llm_preserver.cli.verify_cmd import ProgressRenderer
from llm_preserver.hashing import sha256_of
from llm_preserver.verify import ProgressEvents, verify_archive

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"


class FakeTty(io.StringIO):
    """A StringIO that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def hex_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def entry_for(rel_path: str, content: bytes, **overrides: object) -> dict[str, object]:
    made: dict[str, object] = {
        "path": rel_path,
        "sha256": hex_of(content),
        "size": len(content),
        "source": "original",
    }
    made.update(overrides)
    return made


@pytest.fixture
def built_archive(
    tmp_path: Path,
    write_model: Callable[..., Path],
    sample_record_dict: Callable[..., dict],
) -> Path:
    """An archive holding one intact single-file model."""
    root = tmp_path / "archive"
    init_archive(root)
    record = sample_record_dict()
    record["artifacts"][0]["files"] = [entry_for(PAYLOAD_REL, PAYLOAD)]
    model_dir = write_model(root, record)
    target = model_dir / PAYLOAD_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PAYLOAD)
    return root


# --- hashing progress feed ---


def test_sha256_of_reports_every_chunk(tmp_path: Path) -> None:
    content = b"x" * ((1 << 20) + 17)  # two chunks
    target = tmp_path / "blob.bin"
    target.write_bytes(content)
    seen: list[int] = []

    digest = sha256_of(target, progress=seen.append)

    assert digest == hashlib.sha256(content).hexdigest()
    assert sum(seen) == len(content)
    assert len(seen) == 2


# --- core event firing ---


def test_core_fires_model_and_file_events_in_order(built_archive: Path) -> None:
    log: list[tuple[str, object]] = []
    events = ProgressEvents(
        on_model_start=lambda mid, files, size: log.append(("model", (mid, files, size))),
        on_file_start=lambda rel, size: log.append(("file", (rel, size))),
        on_file_bytes=lambda count: log.append(("bytes", count)),
    )

    verify_archive(built_archive, events=events)

    assert log[0] == ("model", ("acme/tiny-chat", 1, len(PAYLOAD)))
    assert log[1] == ("file", (PAYLOAD_REL, len(PAYLOAD)))
    assert ("bytes", len(PAYLOAD)) in log


def test_quick_run_fires_no_file_events(built_archive: Path) -> None:
    log: list[str] = []
    events = ProgressEvents(
        on_model_start=lambda mid, files, size: log.append("model"),
        on_file_start=lambda rel, size: log.append("file"),
        on_file_bytes=lambda count: log.append("bytes"),
    )

    verify_archive(built_archive, quick=True, events=events)

    assert log == ["model"]  # quick never hashes, so no file/byte events


# --- renderer ---


def test_renderer_writes_nothing_when_stream_is_not_a_tty() -> None:
    stream = io.StringIO()  # isatty() is False
    renderer = ProgressRenderer(stream, now=FakeClock())

    renderer.on_model_start("acme/tiny-chat", 1, 18)
    renderer.on_file_start(PAYLOAD_REL, 18)
    renderer.on_file_bytes(18)
    renderer.finish_line()

    assert stream.getvalue() == ""


def test_renderer_prints_checking_line_per_model_on_a_tty() -> None:
    stream = FakeTty()
    renderer = ProgressRenderer(stream, now=FakeClock())

    renderer.on_model_start("acme/tiny-chat", 3, 29 * (1 << 30))

    out = stream.getvalue()
    assert "checking acme/tiny-chat" in out
    assert "3 files" in out
    assert "29.0 GiB recorded" in out


def test_renderer_updates_the_byte_counter_in_place_and_throttles() -> None:
    stream = FakeTty()
    clock = FakeClock()
    renderer = ProgressRenderer(stream, now=clock)

    renderer.on_file_start(PAYLOAD_REL, 4 * (1 << 30))
    clock.value = 1.0
    renderer.on_file_bytes(1 << 30)  # past the interval: renders
    renderer.on_file_bytes(1 << 30)  # same instant: throttled
    clock.value = 2.0
    renderer.on_file_bytes(1 << 30)  # renders again
    renderer.finish_line()

    out = stream.getvalue()
    assert f"hashing {PAYLOAD_REL}" in out
    assert "1.0 GiB / 4.0 GiB" in out
    assert "3.0 GiB / 4.0 GiB" in out
    assert "2.0 GiB / 4.0 GiB" not in out  # the throttled update
    assert "\r" in out  # in-place, not scrolling
    assert out.endswith("\n")  # finish_line terminated the counter


def test_renderer_scrubs_hostile_paths() -> None:
    stream = FakeTty()
    renderer = ProgressRenderer(stream, now=FakeClock())

    renderer.on_file_start("gguf/evil\x1b[2Jname.gguf", 10)

    assert "\x1b" not in stream.getvalue()
