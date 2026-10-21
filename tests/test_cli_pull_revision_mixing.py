"""A ``--revision`` pull never merges a second commit into an artifact (spec 0024).

Review-round finding, adjudicated 2026-10-20 ("refuse it"): two
revisions whose files do not collide by name — different shard counts,
or a same-size hashless ``config.json`` — used to merge silently at
exit 0, leaving a mixed tree whose artifact-level ``revision`` named
only the newest commit. A ``--revision`` pull into an artifact recorded
at a different commit now stops during planning (exit 5) before any
byte moves, naming the archived commit. Default-branch pulls keep their
pre-0024 behavior: ``main`` drifting is the normal add-a-quant-later
workflow.
"""

from revision_shapes import (
    BRANCH,
    BRANCH_FILES,
    BRANCH_SHA,
    MAIN_SHA,
    REPO_ID,
    TREES,
    full_output,
    init_archive_dir,
    install_fake_hub,
    invoke_pull,
    model_dir,
    read_record,
    revision_client,
)

MOVED_SHA = "c" * 40


def _tree_snapshot(archive):
    return sorted(
        (str(p.relative_to(archive)), p.read_bytes() if p.is_file() else b"")
        for p in archive.rglob("*")
    )


def _seed(archive, monkeypatch, fake_hub_factory, *args):
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))
    seeded = invoke_pull(archive, *args)
    assert seeded.exit_code == 0, full_output(seeded)


def test_a_revision_pull_into_an_artifact_at_another_commit_is_refused(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    _seed(archive, monkeypatch, fake_hub_factory, "--include", "*cal*", "--yes")
    before = _tree_snapshot(archive)
    client = revision_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--include", "*model*", "--yes", "--revision", BRANCH)

    text = full_output(result)
    assert result.exit_code == 5, text
    assert "error [integrity]" in text
    assert MAIN_SHA in text  # names the archived commit...
    assert f"--revision {MAIN_SHA}" in text  # ...and how to extend that tree
    assert "Traceback" not in text
    assert client.download_calls == []
    assert _tree_snapshot(archive) == before


def test_plan_reports_the_same_refusal_and_writes_nothing(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    _seed(archive, monkeypatch, fake_hub_factory, "--include", "*cal*", "--yes")
    before = _tree_snapshot(archive)
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--whole-repo", "--plan", "--revision", BRANCH)

    assert result.exit_code == 5, full_output(result)
    assert MAIN_SHA in full_output(result)
    assert _tree_snapshot(archive) == before


def test_a_moved_branch_is_refused_and_names_the_archived_commit(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The branch advanced after the first pull: a re-pull by branch name
    # would mix the old and new heads, so it stops; the archived commit
    # is the way to keep extending the tree that is already there.
    archive = init_archive_dir(tmp_path)
    _seed(
        archive,
        monkeypatch,
        fake_hub_factory,
        "--include",
        "*model*",
        "--yes",
        "--revision",
        BRANCH,
    )
    moved = dict(TREES)
    moved[BRANCH] = (MOVED_SHA, [("model.safetensors", b"retrained bytes!!!!!!!!!!", True)])
    moved[MOVED_SHA] = moved[BRANCH]
    client = revision_client(fake_hub_factory, trees=moved)
    install_fake_hub(monkeypatch, client)

    result = invoke_pull(archive, "--whole-repo", "--yes", "--revision", BRANCH)

    text = full_output(result)
    assert result.exit_code == 5, text
    assert f"--revision {BRANCH_SHA}" in text
    assert client.download_calls == []


def test_the_archived_commit_extends_its_own_artifact(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    _seed(
        archive,
        monkeypatch,
        fake_hub_factory,
        "--include",
        "*model*",
        "--yes",
        "--revision",
        BRANCH,
    )
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    result = invoke_pull(archive, "--whole-repo", "--yes", "--revision", BRANCH_SHA)

    assert result.exit_code == 0, full_output(result)
    record = read_record(archive)
    pins = {entry["revision"] for a in record["artifacts"] for entry in a["files"]}
    assert pins == {BRANCH_SHA}
    assert {name for name, _b, _l in BRANCH_FILES} <= {
        entry["path"].rsplit("/", 1)[-1] for a in record["artifacts"] for entry in a["files"]
    }


def test_a_default_branch_pull_keeps_its_pre_0024_merge(tmp_path, monkeypatch, fake_hub_factory):
    # Differential against the refusal above: the same two trees, but the
    # second pull names no revision, so nothing new is enforced.
    archive = init_archive_dir(tmp_path)
    _seed(
        archive,
        monkeypatch,
        fake_hub_factory,
        "--include",
        "*model*",
        "--yes",
        "--revision",
        BRANCH,
    )
    install_fake_hub(monkeypatch, revision_client(fake_hub_factory))

    # --refresh-docs clears the unrelated spec 0020 doc stop (the two
    # READMEs differ), isolating the guard under test.
    result = invoke_pull(archive, "--include", "*cal*", "--yes", "--refresh-docs")

    assert result.exit_code == 0, full_output(result)
    assert (model_dir(archive)).exists()
    assert REPO_ID.split("/")[1] in str(model_dir(archive))
