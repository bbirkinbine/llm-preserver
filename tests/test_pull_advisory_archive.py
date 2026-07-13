"""Tests for pull_advisory.archived_hub_repos (spec 0005).

Split from test_pull_advisory.py (file-size cap): the archive-scan
half of the advisory engine — which hub repos count as archived, and
how unreadable records are handled.
"""

from llm_preserver.archive import init_archive
from llm_preserver.pull_advisory import archived_hub_repos
from llm_preserver.records import ArtifactEntry, ModelRecord, save_record


def _save_model(archive, creator: str, model: str, record: ModelRecord):
    model_dir = archive / "models" / creator / model
    model_dir.mkdir(parents=True)
    save_record(record, model_dir)


def _record(name: str, hub_id: str, source_repo: str) -> ModelRecord:
    return ModelRecord(
        name=name,
        hub_id=hub_id,
        artifacts=[ArtifactEntry(format="gguf", provenance="verified", source_repo=source_repo)],
    )


def test_archived_hub_repos_returns_source_repo_ids_from_every_record(tmp_path):
    archive = tmp_path / "archive"
    init_archive(archive)
    _save_model(
        archive,
        "acme",
        "tiny-chat",
        _record("tiny-chat", "acme/tiny-chat", "https://huggingface.co/bartowski/tiny-chat-GGUF"),
    )
    _save_model(
        archive,
        "Qwen",
        "Qwen3-0.6B",
        _record("Qwen3-0.6B", "Qwen/Qwen3-0.6B", "https://huggingface.co/Qwen/Qwen3-0.6B"),
    )

    repos = archived_hub_repos(archive)

    assert {"bartowski/tiny-chat-GGUF", "Qwen/Qwen3-0.6B"} <= repos


def test_hub_id_alone_does_not_mark_the_master_repo_archived(tmp_path):
    # A quant-only pull records hub_id = the original model's id, but
    # only the quant repo's files are archived — the full-precision
    # master is still missing, so its advisory must keep firing.
    archive = tmp_path / "archive"
    init_archive(archive)
    _save_model(
        archive,
        "acme",
        "tiny-chat",
        _record("tiny-chat", "acme/tiny-chat", "https://huggingface.co/bartowski/tiny-chat-GGUF"),
    )

    assert "acme/tiny-chat" not in archived_hub_repos(archive)


def test_archived_hub_repos_of_fresh_archive_is_empty(tmp_path):
    archive = tmp_path / "archive"
    init_archive(archive)

    assert archived_hub_repos(archive) == set()


def test_unreadable_record_is_skipped_not_fatal(tmp_path):
    # An unreadable record is not evidence of an archived repo, and the
    # advisory scan must never abort a pull over someone else's
    # corruption — the readable records still count.
    archive = tmp_path / "archive"
    init_archive(archive)
    _save_model(
        archive,
        "acme",
        "tiny-chat",
        _record("tiny-chat", "acme/tiny-chat", "https://huggingface.co/bartowski/tiny-chat-GGUF"),
    )
    broken_dir = archive / "models" / "broken" / "model"
    broken_dir.mkdir(parents=True)
    (broken_dir / "model-record.json").write_text("{not json", encoding="utf-8")

    repos = archived_hub_repos(archive)

    assert "bartowski/tiny-chat-GGUF" in repos
