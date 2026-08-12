"""Core tests for verify's ADR-0003 layout verdict — spec 0017 pass 1.

``verify_archive`` gains one judgment beyond fixity: does this
directory obey ``path == hub_id == every artifact's source_repo``?
The contract these tests pin:

* ``ModelVerifyResult.offending_repo`` names the disagreeing repo
  whenever the layout is wrong, and is None otherwise.
* ``layout`` is a field of its own — ``ok`` or ``unmigrated`` — beside
  the fixity ``state``, never in place of it. Collapsing the two would
  erase spec 0009's complete-vs-valid distinction.
* When a model is *both* unmigrated and drifted, drift wins the exit
  code (5 over 1); the layout verdict is still reported.
* The verdict is derived from the record alone, so it costs no hashing.

Every record here states its ``hub_id`` and ``source_repo``
explicitly — this suite is about the three-way invariant, so no test
may inherit whatever shape the shared fixture happens to encode.
CLI report text and exit codes live in test_cli_verify_layout.py.
"""

import hashlib
import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from llm_preserver.archive import init_archive
from llm_preserver.verify import verify_archive

PAYLOAD_REL = "gguf/tiny-chat-Q4_K_M.gguf"
PAYLOAD = b"good payload bytes"
EVIL = b"evil payload bytes"  # same length as PAYLOAD: only the hash differs

OWN_URL = "https://huggingface.co/acme/tiny-chat"
FOREIGN_REPO = "other/tiny-chat-GGUF"
FOREIGN_URL = "https://huggingface.co/other/tiny-chat-GGUF"


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    """An initialized (empty) archive under tmp_path."""
    root = tmp_path / "archive"
    init_archive(root)
    return root


@pytest.fixture
def build_model(
    write_model: Callable[..., Path], sample_record_dict: Callable[..., dict]
) -> Callable[..., Path]:
    """Create a model dir whose record states hub_id and source_repo.

    ``source_repo`` is a required keyword: this suite is about the
    three-way invariant, so no test may inherit the fixture's default.
    """

    def _build(
        archive: Path,
        *,
        source_repo: str | None,
        creator: str = "acme",
        model: str = "tiny-chat",
        payload: bytes = PAYLOAD,
    ) -> Path:
        record = sample_record_dict(name=model, hub_id=f"{creator}/{model}")
        record["artifacts"][0]["source_repo"] = source_repo
        record["artifacts"][0]["files"] = [
            {
                "path": PAYLOAD_REL,
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "size": len(PAYLOAD),
                "source": "original",
            }
        ]
        model_dir = write_model(archive, record, creator=creator, model=model)
        target = model_dir / PAYLOAD_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return model_dir

    return _build


@pytest.fixture
def hash_calls(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every path hashed through the ``llm_preserver.hashing`` seam."""
    hashing = importlib.import_module("llm_preserver.hashing")
    calls: list[Path] = []
    real = hashing.sha256_of

    def counting(path: Path, progress: Callable[[int], None] | None = None) -> str:
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(hashing, "sha256_of", counting)
    return calls


def one_result(archive_root: Path, **kwargs: object) -> Any:  # Any: surface under test-first
    """Run verify_archive and return the single per-model result."""
    results = verify_archive(archive_root, **kwargs).models
    assert len(results) == 1
    return results[0]


def test_directory_holding_a_foreign_repos_files_reports_unmigrated(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive_root, source_repo=FOREIGN_URL)

    assert one_result(archive_root).layout == "unmigrated"


def test_unmigrated_result_names_the_offending_source_repo(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive_root, source_repo=FOREIGN_URL)

    assert one_result(archive_root).offending_repo == FOREIGN_REPO


def test_directory_whose_record_matches_its_path_stays_valid(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    build_model(archive_root, source_repo=OWN_URL)

    result = one_result(archive_root)

    assert result.state == "valid"
    assert result.offending_repo is None


def test_unmigrated_on_its_own_is_not_drift(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    # A misfiled directory is a layout problem, not damage: it must not
    # borrow spec 0009's drift signal (which the CLI maps to exit 5).
    build_model(archive_root, source_repo=FOREIGN_URL)

    assert verify_archive(archive_root).drifted is False


def test_a_drifted_unmigrated_model_keeps_the_drift_state(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    # Drift wins: a broken hash is damage and outranks tidiness, so the
    # fixity verdict is what the state (and the exit code) reports.
    build_model(archive_root, source_repo=FOREIGN_URL, payload=EVIL)

    result = one_result(archive_root)

    assert result.state == "invalid"
    assert verify_archive(archive_root).drifted is True


def test_a_drifted_unmigrated_model_still_names_the_offending_repo(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    # Losing the precedence contest must not hide the layout problem.
    build_model(archive_root, source_repo=FOREIGN_URL, payload=EVIL)

    assert one_result(archive_root).offending_repo == FOREIGN_REPO


def test_quick_run_reaches_the_verdict_without_hashing_anything(
    archive_root: Path, build_model: Callable[..., Path], hash_calls: list[Path]
) -> None:
    # The verdict is a record read, so --quick reaches it for free.
    build_model(archive_root, source_repo=FOREIGN_URL)

    result = one_result(archive_root, quick=True)

    assert result.layout == "unmigrated"
    assert hash_calls == []


def test_each_directory_is_judged_on_its_own(
    archive_root: Path, build_model: Callable[..., Path]
) -> None:
    # Half-converted is an expected state (migration is resumable and
    # 683 GiB of renames is not instantaneous), so a mixed archive must
    # report each directory honestly rather than condemning both.
    build_model(archive_root, source_repo=OWN_URL)
    build_model(
        archive_root,
        source_repo=FOREIGN_URL,
        creator="beta",
        model="coder",
    )

    results = {result.model_id: result for result in verify_archive(archive_root).models}

    # Both axes, per directory: the converted model is clean on layout
    # and still carries its own fixity verdict.
    assert {model_id: r.layout for model_id, r in results.items()} == {
        "acme/tiny-chat": "ok",
        "beta/coder": "unmigrated",
    }
    assert {model_id: r.state for model_id, r in results.items()} == {
        "acme/tiny-chat": "valid",
        "beta/coder": "valid",
    }
