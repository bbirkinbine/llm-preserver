"""Shared repo shapes and helpers for the spec 0024 ``--revision`` tests.

Mirrors ``migrate_shapes.py``: plain helpers, no fixtures. The repo is
a miniature of the 2026-10-20 trigger (``turboderp/GLM-5.3-Flash-exl3``):
the default branch holds calibration material, while the deployable
model lives on the ``4.05bpw`` branch. A pull that reads the wrong tree
archives the wrong files, which is how these tests tell the trees apart.

``revision_client`` subclasses the shared ``FakeHubClient`` (reached
through the ``fake_hub_factory`` fixture rather than importing
conftest) so each revision serves its own tree. Its ``download`` serves
bytes only for a resolved commit; given branch text it fails the test
outright, which is criterion 9's invariant enforced by the fake itself.
Every archive lives in ``tmp_path``; never a real one.
"""

import contextlib
import json
import logging
from pathlib import Path

import click
from typer.testing import CliRunner

from llm_preserver.cli import app

runner = CliRunner()

REPO_ID = "turboderp/tiny-exl3"
BRANCH = "4.05bpw"
BRANCH_SHA = "2a30229e67012798ba9f0cd832bb78abf4c363d5"
MAIN_SHA = "ef396bc0" + "1" * 32

# (repo-relative path, bytes, is_lfs) — the conftest triple shape. The
# READMEs differ in length on purpose: the doc-stop test needs the
# branch's card to read as changed against the archived main card.
MAIN_FILES = [
    ("cal_trace.safetensors", b"calibration trace bytes", True),
    ("qbench_prompts.json", b"[]", False),
    ("README.md", b"# calibration\n", False),
]
BRANCH_FILES = [
    ("model.safetensors", b"exl3 4.05bpw weight bytes", True),
    ("config.json", b"{}", False),
    ("chat_template.jinja", b"{{ messages }}", False),
    ("README.md", b"# tiny exl3, 4.05bpw branch\n", False),
]
TREES = {
    None: (MAIN_SHA, MAIN_FILES),
    BRANCH: (BRANCH_SHA, BRANCH_FILES),
    BRANCH_SHA: (BRANCH_SHA, BRANCH_FILES),
}


def revision_client(fake_hub_factory, trees=TREES, **overrides):
    """A ``FakeHubClient`` whose tree and commit depend on the revision.

    Args:
        fake_hub_factory: The conftest fixture (supplies the base class).
        trees: Requested revision (None = default branch) -> (commit,
            files). A revision outside it raises the user-fault error
            the real client maps ``RevisionNotFoundError`` to.
        **overrides: Passed through to ``FakeHubClient``.
    """
    base_cls = type(fake_hub_factory())
    by_commit = {commit: files for commit, files in trees.values()}

    class RevisionHubClient(base_cls):
        def repo_info(self, repo_id, revision=None):
            from llm_preserver.hub import PullUserError

            if revision not in trees:
                self.repo_info_revisions.append(revision)
                raise PullUserError(f"repo, revision, or file not found: {revision}")
            self.commit, files = trees[revision]
            self.files = list(files)
            return super().repo_info(repo_id, revision)

        def download(self, repo_id, filename, revision, dest_dir):
            if revision not in by_commit:
                raise AssertionError(f"download got {revision!r}, not a resolved commit")
            self.files = list(by_commit[revision])
            return super().download(repo_id, filename, revision, dest_dir)

    overrides.setdefault("base_model", None)
    main_commit, main_files = trees[None]
    return RevisionHubClient(files=main_files, repo_id=REPO_ID, commit=main_commit, **overrides)


def init_archive_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client) -> None:
    """Swap the CLI's hub-client seam for a fake (test_cli_pull.py pattern)."""
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def invoke_pull(archive: Path, *args: str, stdin: str | None = None, repo: str = REPO_ID):
    """Run one pull with a fresh log handler (see test_pull_doc_recovery_guards)."""
    logging.getLogger("llm_preserver").handlers.clear()
    return runner.invoke(app, ["pull", repo, str(archive), *args], input=stdin)


def stdout_text(result) -> str:
    """Unstyled ``result.output`` alone — the only safe text to count lines in."""
    return click.unstyle(result.output)


def full_output(result) -> str:
    """Everything the run printed, unstyled (substring asserts only)."""
    text = click.unstyle(result.output)
    with contextlib.suppress(ValueError, AttributeError):
        text += click.unstyle(result.stderr)
    return text


def revision_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("revision:")]


def model_dir(archive: Path) -> Path:
    return archive / "models" / "turboderp" / "tiny-exl3"


def read_record(archive: Path) -> dict:
    return json.loads((model_dir(archive) / "model-record.json").read_text())


def recorded_names(record: dict) -> set[str]:
    """Basenames of every file entry across the record's artifacts."""
    return {
        Path(entry["path"]).name for artifact in record["artifacts"] for entry in artifact["files"]
    }
