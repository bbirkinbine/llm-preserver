"""Unit behavior of ``llm_preserver.pull_report.render_plan`` (spec 0005).

``render_plan(prep, would_ask)`` is pure: a ``PullPreparation`` plus
the questions a real pull would have asked in, printable lines out, no
I/O. These tests pin content presence — hub paths with sizes, the
total, the target directory, ``would ask:`` rendering, advisory text,
the no-download closing line — never exact layout. Imports of the
modules under test happen lazily inside test bodies so collection
never depends on the implementation existing (conftest convention).
"""

FULL_COMMIT_HASH = "a" * 40

REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_PATH = "tiny-chat-Q4_K_M.gguf"
README_PATH = "README.md"
Q4_SIZE = 15
README_SIZE = 22


def make_prep(
    tmp_path, *, to_download_paths=(Q4_PATH, README_PATH), advisories=(), needed_bytes=37
):
    """Build a hand-rolled PullPreparation over the conftest repo shape."""
    from llm_preserver.hub import RepoFile, RepoInfo
    from llm_preserver.pull_plan import PlannedDownload, PullPlan
    from llm_preserver.pull_prepare import PullPreparation

    q4 = RepoFile(path=Q4_PATH, size=Q4_SIZE, sha256="0" * 64)
    readme = RepoFile(path=README_PATH, size=README_SIZE, sha256=None)
    selected = [q4, readme]
    by_path = {repo_file.path: repo_file for repo_file in selected}
    plan = PullPlan(
        to_download=[
            PlannedDownload(repo_file=by_path[path], target_rel=f"gguf/{path}")
            for path in to_download_paths
        ],
        adopted=[],
    )
    info = RepoInfo(
        commit=FULL_COMMIT_HASH,
        files=selected,
        base_model=None,
        pipeline_tag="text-generation",
        license="apache-2.0",
    )
    return PullPreparation(
        repo_id=REPO_ID,
        info=info,
        creator="acme",
        name="tiny-chat",
        model_dir=tmp_path / "models" / "acme" / "tiny-chat",
        subdir="gguf",
        selected=selected,
        plan=plan,
        needed_bytes=needed_bytes,
        disk_free=10**9,
        advisories=list(advisories),
        staging_dir=tmp_path / ".staging" / "acme" / "tiny-chat",
        select_all=False,
    )


def test_lists_each_download_with_size_total_and_target(tmp_path):
    from llm_preserver.pull_report import render_plan

    prep = make_prep(tmp_path)

    lines = render_plan(prep, [])

    text = "\n".join(lines)
    assert any(Q4_PATH in line and str(Q4_SIZE) in line for line in lines)
    assert any(README_PATH in line and str(README_SIZE) in line for line in lines)
    assert "37 B" in text
    assert str(prep.model_dir) in text
    assert REPO_ID in text


def test_renders_each_would_ask_question_on_its_own_line(tmp_path):
    from llm_preserver.pull_report import render_plan

    question = f"group {REPO_ID} under canonical model acme/tiny-chat?"

    lines = render_plan(make_prep(tmp_path), [question])

    matches = [line for line in lines if line.lstrip().startswith("would ask:")]
    assert len(matches) == 1
    assert question in matches[0]


def test_no_would_ask_lines_when_nothing_would_be_asked(tmp_path):
    from llm_preserver.pull_report import render_plan

    lines = render_plan(make_prep(tmp_path), [])

    assert not any("would ask:" in line for line in lines)


def test_closing_line_states_nothing_downloaded(tmp_path):
    from llm_preserver.pull_report import render_plan

    lines = render_plan(make_prep(tmp_path), [])

    assert "nothing downloaded" in lines[-1]


def test_marks_selected_but_not_downloading_files_already_archived(tmp_path):
    from llm_preserver.pull_report import render_plan

    # README is selected but not planned for download (and not adopted):
    # the report shows it as an already-archived skip.
    prep = make_prep(tmp_path, to_download_paths=(Q4_PATH,), needed_bytes=Q4_SIZE)

    text = "\n".join(render_plan(prep, []))

    assert "already archived" in text
    assert README_PATH in text


def test_warning_severity_renders_with_warning_prefix(tmp_path):
    # Human-error findings (grouping mismatch) must stand out from
    # missing-companion advisories: distinct prefix, not one uniform
    # "advisory:" wall.
    from llm_preserver.pull_advisory import Advisory
    from llm_preserver.pull_report import render_plan

    warning = Advisory(
        kind="grouping mismatch",
        message="this repo declares base model a/b, but --model files it under c/d",
        severity="warning",
    )
    advisory = Advisory(kind="vision projector", message="tree ships mmproj-F16.gguf")

    lines = render_plan(make_prep(tmp_path, advisories=[warning, advisory]), [])

    warning_lines = [line for line in lines if line.startswith("warning:")]
    advisory_lines = [line for line in lines if line.startswith("advisory:")]
    assert len(warning_lines) == 1
    assert "c/d" in warning_lines[0]
    assert len(advisory_lines) == 1


def test_advisory_messages_appear_in_the_report(tmp_path):
    from llm_preserver.pull_advisory import Advisory
    from llm_preserver.pull_report import render_plan

    advisory = Advisory(
        kind="vision projector",
        message=(
            "tree ships mmproj-F16.gguf (vision projector); the selection "
            "excludes it — add --include '*mmproj-F16.gguf'"
        ),
    )

    text = "\n".join(render_plan(make_prep(tmp_path, advisories=[advisory]), []))

    assert "mmproj-F16.gguf" in text
    assert "vision projector" in text


def test_adapter_fetch_is_disclosed_in_the_report(tmp_path):
    # The one adjudicated exception to "downloads nothing" must appear
    # in the report itself, and the closing line must stay honest.
    import dataclasses

    from llm_preserver.pull_report import render_plan

    prep = dataclasses.replace(make_prep(tmp_path), adapter_config_fetched=True)

    lines = render_plan(prep, [])

    assert any("fetched adapter_config.json" in line and "advisory only" in line for line in lines)
    assert "no weights downloaded" in lines[-1]
    assert "nothing written" in lines[-1]


def test_render_plan_performs_no_io(tmp_path, capsys):
    from llm_preserver.pull_report import render_plan

    render_plan(make_prep(tmp_path), ["a question?"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
