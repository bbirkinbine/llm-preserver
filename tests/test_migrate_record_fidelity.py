"""What a migration carries and what it must not duplicate.

Two review findings (2026-08-11), both silent:

* A resumed run appended the artifact a second time — `_write_target_record`
  had no dedup on the `(format, source_repo)` key that `update_record`
  uses. `verify` calls the result `valid` (each entry hashes fine) and
  `status` doubles the size, so it would never surface.
* A pure rename rebuilt the target record from five fields, dropping
  everything else and then deleting the source. `pipeline_tag` was lost
  on all three renames of the live archive; `notes` is documented as
  free-form curator text and would have been unrecoverable.
"""

import json
from pathlib import Path

from migrate_shapes import Q4, Q4_REL, build_directory, init_archive_dir

from llm_preserver.migrate import execute_migration, plan_migration
from llm_preserver.records import load_record

FOREIGN = "unsloth/tiny-chat-GGUF"


def enrich(model_dir: Path, **fields: object) -> None:
    """Set record fields a pull or a curator would have populated."""
    record = json.loads((model_dir / "model-record.json").read_text())
    record.update(fields)
    (model_dir / "model-record.json").write_text(json.dumps(record, indent=2) + "\n")


def test_a_rename_carries_the_whole_record_forward(tmp_path: Path) -> None:
    root = init_archive_dir(tmp_path)
    model_dir = build_directory(root, "Qwen/tiny-chat", [("gguf", FOREIGN, {Q4_REL: Q4})])
    enrich(
        model_dir,
        pipeline_tag="text-generation",
        parameter_count="30B",
        context_length=262144,
        notes="hand-written curator note that exists nowhere else",
        capabilities=["tools", "vision"],
    )

    execute_migration(root, plan_migration(root))

    moved = load_record(root / "models" / FOREIGN)
    assert moved.pipeline_tag == "text-generation"
    assert moved.parameter_count == "30B"
    assert moved.context_length == 262144
    assert moved.notes == "hand-written curator note that exists nowhere else"
    assert moved.capabilities == ["tools", "vision"]


def test_a_resumed_run_does_not_record_the_artifact_twice(tmp_path: Path) -> None:
    import llm_preserver.migrate.execute as execute_module

    root = init_archive_dir(tmp_path)
    build_directory(root, "Qwen/tiny-chat", [("gguf", FOREIGN, {Q4_REL: Q4})])

    # Interrupt after the target record lands, before the source is
    # rewritten — the window plain Ctrl-C hits, and the one the tool
    # tells you to resume from.
    real = execute_module._rewrite_source_record

    def boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    execute_module._rewrite_source_record = boom
    try:
        with __import__("contextlib").suppress(KeyboardInterrupt):
            execute_migration(root, plan_migration(root))
    finally:
        execute_module._rewrite_source_record = real

    execute_migration(root, plan_migration(root))

    moved = load_record(root / "models" / FOREIGN)
    assert len(moved.artifacts) == 1
    assert len(moved.artifacts[0].files) == 1
