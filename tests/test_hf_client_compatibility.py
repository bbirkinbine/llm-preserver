"""Compatibility contract for the installed Hugging Face Hub client.

The dependency-policy tests pin the supported floor, major-version cap,
and reproducible lock selected by spec 0022. The adapter tests stay offline:
they feed real public ``huggingface_hub`` data objects through the production
``HubClient`` while replacing only the network entry points.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from huggingface_hub import ModelInfo
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

import llm_preserver.hub as hub
from llm_preserver.hub import client as hub_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
LOCK_PATH = PROJECT_ROOT / "uv.lock"
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
COMPATIBILITY_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "hf-client-compatibility.yml"
MAINTENANCE_GUIDE_PATH = PROJECT_ROOT / "docs" / "hf-client-maintenance.md"
README_PATH = PROJECT_ROOT / "README.md"


def _project_requirement(package_name: str) -> Requirement:
    project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]
    wanted = canonicalize_name(package_name)
    for raw_requirement in project["dependencies"]:
        requirement = Requirement(raw_requirement)
        if canonicalize_name(requirement.name) == wanted:
            return requirement
    raise AssertionError(f"{package_name} is not a direct runtime dependency")


def _locked_version(package_name: str) -> Version:
    lock = tomllib.loads(LOCK_PATH.read_text(encoding="utf-8"))
    wanted = canonicalize_name(package_name)
    versions = [
        Version(package["version"])
        for package in lock["package"]
        if canonicalize_name(package["name"]) == wanted
    ]
    assert len(versions) == 1, f"expected one locked {package_name} package, found {versions}"
    return versions[0]


def _workflow(path: Path) -> dict:
    assert path.is_file(), f"missing workflow: {path.relative_to(PROJECT_ROOT)}"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    # PyYAML follows YAML 1.1 and reads GitHub Actions' unquoted ``on``
    # key as boolean true. Normalize only that parser mismatch.
    if True in workflow and "on" not in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def _workflow_steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _matrix_text(workflow: dict) -> str:
    matrices = [job.get("strategy", {}).get("matrix", {}) for job in workflow["jobs"].values()]
    return "\n".join(str(matrix) for matrix in matrices)


def test_huggingface_hub_dependency_starts_at_the_security_floor_and_excludes_v2() -> None:
    """Installers outside uv accept qualified 1.x clients, never unqualified 2.x."""
    requirement = _project_requirement("huggingface-hub")

    assert requirement.specifier == SpecifierSet(">=1.26.0,<2")


def test_httpx_is_a_declared_runtime_contract() -> None:
    """The transport exceptions production imports do not ride a transitive dependency."""
    requirement = _project_requirement("httpx")

    assert requirement.specifier == SpecifierSet(">=0.23,<1")


def test_lock_pins_the_qualified_huggingface_hub_release() -> None:
    """Ordinary development and CI resolve the client qualified by spec 0022."""
    assert _locked_version("huggingface-hub") == Version("1.32.0")


def test_lock_uses_an_anyio_release_with_the_known_advisories_fixed() -> None:
    """The lock refresh does not retain the vulnerable anyio 4.14.1 release."""
    assert _locked_version("anyio") >= Version("4.14.2")


class RecordingInfoApi:
    """Return one real ``ModelInfo`` while recording the production call."""

    def __init__(self, info: ModelInfo) -> None:
        self.info = info
        self.calls: list[tuple[str, dict[str, object]]] = []

    def model_info(self, repo_id: str, **kwargs: object) -> ModelInfo:
        self.calls.append((repo_id, kwargs))
        return self.info


def test_repo_info_consumes_real_model_info_with_file_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata call and all consumed ModelInfo fields remain compatible."""
    file_sha256 = "d" * 64
    # Source: https://huggingface.co/docs/huggingface_hub/package_reference/hf_api
    # Retrieved 2026-09-21; Hugging Face Hub documentation is Apache-2.0.
    info = ModelInfo(
        id="acme/tiny-chat",
        sha="a" * 40,
        pipeline_tag="text-generation",
        cardData={"base_model": ["acme/base"], "license": "apache-2.0"},
        siblings=[
            {
                "rfilename": "weights/tiny.gguf",
                "size": 17,
                "lfs": {"size": 17, "sha256": file_sha256, "pointerSize": 128},
            },
            {"rfilename": "README.md", "size": 11},
        ],
    )
    api = RecordingInfoApi(info)
    monkeypatch.setattr(hub_client, "HfApi", lambda: api)

    result = hub.HubClient().repo_info("acme/tiny-chat")

    assert api.calls == [("acme/tiny-chat", {"files_metadata": True})]
    assert result == hub.RepoInfo(
        commit="a" * 40,
        files=[
            hub.RepoFile(path="weights/tiny.gguf", size=17, sha256=file_sha256),
            hub.RepoFile(path="README.md", size=11, sha256=None),
        ],
        base_model="acme/base",
        pipeline_tag="text-generation",
        license="apache-2.0",
    )


def test_download_passes_the_pinned_revision_and_local_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production keeps the revision and local_dir guarantees at the HF boundary."""
    calls: list[dict[str, object]] = []
    downloaded = tmp_path / "weights" / "tiny.gguf"

    def record_download(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(downloaded)

    monkeypatch.setattr(hub_client, "HfApi", lambda: object())
    monkeypatch.setattr(hub_client, "hf_hub_download", record_download)

    result = hub.HubClient().download(
        "acme/tiny-chat",
        "weights/tiny.gguf",
        "b" * 40,
        tmp_path,
    )

    assert calls == [
        {
            "repo_id": "acme/tiny-chat",
            "filename": "weights/tiny.gguf",
            "revision": "b" * 40,
            "local_dir": tmp_path,
        }
    ]
    assert result == downloaded


def test_ordinary_ci_syncs_only_from_the_committed_lock() -> None:
    """Pull-request quality and audit jobs both reject lock drift."""
    sync_commands = [
        step["run"]
        for step in _workflow_steps(_workflow(CI_WORKFLOW_PATH))
        if "run" in step and re.search(r"\buv sync\b", step["run"])
    ]

    assert sync_commands
    assert all(re.search(r"\buv sync\s+--locked\b", command) for command in sync_commands)


def test_compatibility_workflow_runs_weekly_and_on_manual_dispatch() -> None:
    """The floating compatibility signal is scheduled and manually repeatable."""
    triggers = _workflow(COMPATIBILITY_WORKFLOW_PATH)["on"]

    assert "workflow_dispatch" in triggers
    schedules = triggers.get("schedule", [])
    assert schedules
    cron_fields = schedules[0]["cron"].split()
    assert len(cron_fields) == 5
    assert cron_fields[2:4] == ["*", "*"]
    assert cron_fields[4] != "*"


def test_compatibility_matrix_covers_floor_and_latest_1x_and_prints_versions() -> None:
    """Both supported endpoints identify the exact client that produced a result."""
    workflow = _workflow(COMPATIBILITY_WORKFLOW_PATH)
    matrix = _matrix_text(workflow)
    steps = _workflow_steps(workflow)

    assert "floor" in matrix.lower()
    assert "latest" in matrix.lower()
    assert "huggingface-hub==1.26.0" in matrix
    assert "huggingface-hub<2" in matrix
    assert any(
        "version" in step.get("name", "").lower() and "huggingface" in step.get("run", "").lower()
        for step in steps
    )
    install_runs = [
        step["run"] for step in steps if "install locked project" in step.get("name", "").lower()
    ]
    assert install_runs == ['uv sync --locked\nuv pip install --upgrade "${{ matrix.package }}"\n']
    post_override_runs = [step["run"] for step in steps if "uv run" in step.get("run", "")]
    assert post_override_runs
    assert all("uv run --no-sync" in run for run in post_override_runs)


def test_compatibility_workflow_is_anonymous_and_uses_ephemeral_hf_state() -> None:
    """The public canary needs read-only permissions, no secrets, and no ambient token."""
    workflow = _workflow(COMPATIBILITY_WORKFLOW_PATH)
    workflow_text = COMPATIBILITY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets." not in workflow_text.lower()
    assert re.search(r"(?m)^\s*HF_HOME:\s*.*runner\.temp", workflow_text)
    assert re.search(
        r"(?m)^\s*HF_HUB_DISABLE_IMPLICIT_TOKEN:\s*[\"']?(?:1|true)[\"']?\s*$",
        workflow_text,
        flags=re.IGNORECASE,
    )


def test_compatibility_workflow_separates_install_metadata_and_download_failures() -> None:
    """Step names and test targets identify which canary phase failed."""
    steps = _workflow_steps(_workflow(COMPATIBILITY_WORKFLOW_PATH))
    named_runs = [(step.get("name", "").lower(), step.get("run", "")) for step in steps]

    assert any("install" in name for name, _run in named_runs)
    assert any("metadata" in name and "discovery" in name for name, _run in named_runs)
    assert any("download" in name for name, _run in named_runs)
    assert any("audit" in name and "pip-audit" in run for name, run in named_runs)
    assert any(
        "test_hf_live_canary.py::test_live_metadata_and_discovery_for_public_gpt2" in run
        for _name, run in named_runs
    )
    assert any(
        "test_hf_live_canary.py::test_live_download_of_pinned_gpt2_config" in run
        for _name, run in named_runs
    )


def test_hf_client_maintenance_guide_covers_the_update_qualification_loop() -> None:
    """Maintainers get the complete release-note, matrix, audit, and live checklist."""
    assert MAINTENANCE_GUIDE_PATH.is_file(), "missing docs/hf-client-maintenance.md"
    guide = MAINTENANCE_GUIDE_PATH.read_text(encoding="utf-8").lower()

    for required in (
        "github.com/huggingface/huggingface_hub/releases",
        "1.26.0",
        "<2",
        "pip-audit",
        "workflow_dispatch",
        "authentication",
        "gated",
        "transport",
    ):
        assert required in guide


def test_readme_links_the_hf_client_maintenance_guide() -> None:
    """The development entry point makes the update runbook discoverable."""
    readme = README_PATH.read_text(encoding="utf-8")

    assert "(docs/hf-client-maintenance.md)" in readme
